# Plan: Engineer cloud workspaces on Railway Sandboxes

**Status:** Approved by the owner on 2026-11-25. The broker is built (`aura_sandbox_broker/`).
Gate S2 is met again with the rebuilt proxy (decisions 12 and 13; 87 of 87
checks, [report](../reports/railway-sandbox-spike-2026-11-25.md)). The official SDK is unproven;
S6 and S7 are not measured.

PR 2 (the backend runner and git transfer) is built and tested against a stand-in for the broker,
not against a deployed broker; see [As built: PR 2](#as-built-pr-2). **Do not set
`AURA_SANDBOX_PROVIDER=railway` before PR 3.**
**Base:** `main` at `12eabb00`. Branch `anas/railway-sandboxes`.

## Goal

Run the Engineer agent's server-side sandbox on Railway Sandboxes, for staff and paying
customers. Production runs on Railway, which has no Docker daemon, so no server sandbox runs
there today.

## User decisions (do not re-ask)

Made 2026-11-25.

1. Staff roles **and** paying customers (plans with `engineer`: Pro, Team) can use cloud sandboxes.
2. A **small Node service** (the broker) wraps Railway's official TypeScript SDK. Only it holds
   the Railway token.
3. A workspace is **checkpointed when a turn ends**, the sandbox is destroyed, and the next turn
   boots from the checkpoint.
4. The **egress allowlist is rebuilt inside the sandbox**. If it cannot be enforced, the feature
   stays off.
5. **Railway Pro.** Per person: 2 saved workspaces, 1 running, 2 vCPU / 2 GB. Near the shared
   checkpoint cap, the oldest idle workspaces expire.
6. **Monthly sandbox hours per plan** (setting, starting at 20 per seat) plus a platform-wide
   monthly cap.
7. Each person **connects their own GitHub**. Git with their token runs outside the sandbox.
8. A **desktop / cloud switch per conversation** in the Flutter app. Default: desktop if one is
   paired, otherwise cloud. Fixed once the conversation has changed files. Choosing an offline
   desktop is an error, never a silent move to cloud.
9. An unused workspace is deleted **72 hours** after last use. Account erasure deletes it at once.
10. A **new consent notice**, shown only to people who use the cloud.
11. Built on **`main`**; the new runner also offers the extra methods PR #282 adds, to ease the
    later merge.
12. The sandbox proxy is the broker's own and requires the **TLS server name** to match the host.
13. For `pypi.org`, `files.pythonhosted.org` and `objects.githubusercontent.com` it also
    **inspects the traffic** and refuses a different `Host` header.

Already recorded, unchanged: confirmation gates the workspace boundary (ADR-006, ADR-014); the
GitHub token never enters the sandbox (ADR-013).

## Defaults chosen without asking (change any before approval)

| Default | Value |
|---|---|
| Sandbox Railway project | A new project holding nothing but sandboxes. The broker runs in the production project, reachable only over private networking, with a **project token** for the sandbox project. |
| Sandbox network | Always `ISOLATED`. `--private-network` is never used: it reaches Postgres and Redis. No public domains, no variables passed in. |
| Allowed outbound hosts | The seven in `deploy/sandbox/filter` today. |
| Region | `us-west2`. |
| Platform caps | 200 sandbox-hours a month; 20 running at once. |
| Staff | No per-person hour allowance; counted in the platform cap; same workspace limits. |
| Allowance runs out mid-command | The command finishes; the next is refused. |
| Team plan | Hours pooled across the team. Workspaces stay per person. |
| Downgrade | Access stops at once; saved workspaces expire at 72 hours. |
| Docker runner | Kept for the VPS. `AURA_SANDBOX_PROVIDER=docker` stays the default. |
| Customer rollout | Separate flag `AURA_SANDBOX_CUSTOMERS_ENABLED=false` until staff verification passes. |
| Older app versions | No `execution_target` sent: desktop if paired (offline is an error). Cloud only if cloud consent is already on record; otherwise the refusal says to update the app. |
| No GitHub connected | Empty workspaces work; clone and push are refused with a "connect GitHub" message. |

## Phase 0: proof on a real sandbox (gate)

Throwaway scripts, one sandbox at a time, in the new sandbox project, each destroyed afterwards.
Costs money (well under $5 expected). Result: `docs/reports/railway-sandbox-spike-<date>.md`.

| # | Must show | If it fails |
|---|---|---|
| S1 | Commands run as an unprivileged user that cannot become root or change the firewall | Stop |
| S2 | Outbound reaches only the allowlist, over IPv4 and IPv6, and an unprivileged command cannot send DNS or raw traffic around the proxy | Stop |
| S3 | `ISOLATED` reaches no private service or other sandbox; no Railway token or credential endpoint is readable inside | Stop |
| S4 | Checkpoint of a 1 GB workspace within 60 s; boot from it within 30 s | Report back; revisit decision 3 |
| S5 | 100 MB file transfer both ways; streamed output, timeout and cancel work | Report back |
| S6 | API calls per command fit 10,000 an hour at 20 running sandboxes | Report back |
| S7 | What a checkpoint costs, from the usage page | Report back |

## Broker (`aura_sandbox_broker/`, new; Node 22, TypeScript, `railway` pinned exactly)

- Private HTTP API with a shared bearer secret: create (template or checkpoint), exec (streamed
  NDJSON), file read and write, checkpoint create and delete, destroy, list, health.
- **Policy lives here**, as in today's Docker broker. Callers choose none of: network mode, size,
  region, idle timeout, domains, variables, user. Every command is wrapped to drop privileges.
- After every boot it applies the firewall and proxy from its own copy (not from the sandbox
  disk), then probes one allowed and one refused host. Any failure destroys the sandbox.
- Template mirrors `deploy/sandbox/Dockerfile` (git, python3, build-essential, node, npm) plus
  the proxy and firewall tools.
- Idle timeout 120 minutes as a crash safety net.
- No state and no database access. Logs carry IDs, never command text or file content.
- Dockerfile, `package-lock.json`, unit tests against a fake SDK, `sandbox-broker-ci.yml`.

## Backend (`aura_backend/`)

| Layer | Change |
|---|---|
| domain | Execution target (`desktop`, `cloud`); sandbox usage record; cloud consent version; plan limit `sandbox_hours_per_seat`. |
| ports | Runner capabilities for "transfer a repository in and a bundle out" in place of `workspace_path`; usage and session-target stores. |
| adapters | `railway_sandbox.py` (httpx to the broker; sandbox IDs kept in Postgres, since there are 4 workers); Postgres and in-memory stores. Match PR #282's `read_file_bytes`, `workspace_stats` and `origin`. |
| git | `HostGitService`: clone into a gateway temp folder, upload it; push by taking a bundle from the sandbox into a **fresh** clone with hooks and repo config disabled and objects checked. The gateway never runs git inside a folder the sandbox can write. |
| tools | Capacity becomes a strategy: Docker keeps host-memory and global eviction; cloud uses per-person limits and a Postgres lock. Today's global least-recently-used eviction must **not** apply to cloud, or one customer would delete another's work while the cap has room. Disk quota (2 GB) measured inside the sandbox. |
| kernel and policy | Routing and the "host routed" confirmation rule both follow the execution target (pinned by one test). Checkpoint after the turn in the background; destroy only after the checkpoint succeeds. |
| desktop routing | `clone_repository`, `git_push` and `open_workspace` follow the target too (today clone and push use the server workspace even for paired users). |
| reaper | Gateway periodic task with a Postgres lease (production has no Celery service): checkpoint idle sandboxes, expire at 72 h, destroy orphans in the sandbox project. |
| metering | Minutes from create to destroy; per-team monthly allowance; platform cap. |
| erasure | New step: destroy sandboxes, delete checkpoints, session targets, consent and usage rows. A failed delete keeps the account for retry, like other stores. |
| API | See Contract. |
| config | `AURA_SANDBOX_PROVIDER`, `_BROKER_URL`, `_BROKER_TOKEN`, `_CUSTOMERS_ENABLED`, `_MAX_WORKSPACES_PER_USER`, `_MAX_RUNNING_PER_USER`, `_CHECKPOINT_CAP`, `_MONTHLY_HOURS_PER_SEAT`, `_PLATFORM_MONTHLY_HOURS` (`_MAX_RUNNING` already exists). Boot rule: customers plus sandbox is allowed only with provider `railway` and the customer flag; Docker with customers is still refused. |
| image | Add `git` to `Dockerfile` (it installs none, so server-side git cannot run on Railway today). |

**Migration** (one revision, number set at merge: head is `0033`, and PR #302 also claims
`0034`–`0039`). Additive: `aura_agent_workspaces` gains `provider` (default `docker`),
`sandbox_id`, `checkpoint_name`; new tables for usage, session targets and cloud consent. Existing
rows are unchanged in meaning. `downgrade()` drops what was added.

**Tests:** one runner contract suite run against in-memory and Railway (fake broker); per-person
limits and no cross-customer eviction; failed checkpoint never destroys; routing matches the
confirmation rule; allowance and platform cap; erasure; consent refusal; push from a hostile
bundle; boot rules; migration round trip on a disposable Postgres. Opt-in `-m load` test against a
real broker.

## As built: PR 2

`adapters/railway_sandbox.py`, `adapters/postgres_cloud_sandboxes.py`, the bundle path in
`adapters/host_git.py` and `tools/coding.py`, three settings, `git` in the image, migration `0040`
(`down_revision` `0034`; re-point it if another migration merges first).

Differences from the tables above:

- Sandbox ids and checkpoint names are in their own table, `aura_cloud_sandboxes`, not in
  `aura_agent_workspaces` (which gains only `provider`). The record has to outlive the workspace
  row until the vendor confirms deletion, and single tasks have sandboxes too.
- The Docker runner keeps working on the host path. Only the cloud runner transfers a checkout.
- A workspace boots on its first command or file call, not when it is opened.
- Each save gets a new checkpoint name; the previous one is deleted after the new one is recorded.

Still missing before it can be switched on, all outside PR 2:

- Nothing saves a workspace when a turn ends, and production runs no reaper. Railway destroys an
  idle sandbox after 120 minutes, and unsaved changes go with it.
- Capacity is still the Docker policy: the running and workspace limits are global, so one
  person's request can save or delete another's workspace. A save can also outlast the tool call
  that asked for it, while the process's admission lock is held.
- Account erasure does not yet remove sandboxes or checkpoints.
- Nothing removes sandboxes or checkpoints that lost their record (a crash between a vendor call
  and the write that records it).
- A push moves at most 100 MiB of new commits, and clones the whole repository on the gateway
  within `AURA_SANDBOX_TIMEOUT_S`.

## Client (`aura_client/`)

- Desktop / cloud control beside the composer, extending `engineer_desktop_bar.dart`; the server
  holds the locked choice; "desktop offline" offers a retry and, if no files changed, a manual
  switch.
- Cloud consent sheet, built on `engineer_consent_sheet.dart`.
- Workspaces panel: hours used and left.
- Error states: consent needed, desktop offline, hours used up, no capacity, GitHub not connected.
- English and Arabic strings, RTL check, `flutter gen-l10n`; widget and repository tests.

## Contract (all additive)

| Item | Change |
|---|---|
| `POST /api/v3/chat` (and stream) | Optional `execution_target`: `desktop` or `cloud`. |
| Error codes | `cloud_consent_required`, `desktop_offline`, `execution_target_locked`, `sandbox_hours_exhausted`, `sandbox_capacity`. |
| `GET /api/v3/agents` | New `execution` block: cloud available, consent needed, desktop paired and online, hours used and allowed. |
| `GET /api/v3/engineering/workspaces` | Adds `provider` per workspace and a `usage` block. |
| Cloud consent | `GET`, `POST`, `DELETE /api/v3/engineering/cloud-consent`. |
| Admin console | No change. |

## Rollout order

Every step ships off by default, because each push to `main` deploys to production with no CI gate.

1. Owner: upgrade Railway to Pro; create the sandbox project and its project token.
2. Phase 0 spike and report.
3. PR 1 broker. PR 2 runner and git transfer. PR 3 limits, metering, consent, erasure, routing.
   PR 4 client.
4. Owner: register a GitHub App and set the `AURA_GITHUB_OAUTH_*` variables; approve the consent
   notice wording. Then deploy the broker with no public domain.
5. Turn on for staff. Run `verify-railway-sandboxes` and commit its report.
6. Owner decides; turn on `AURA_SANDBOX_CUSTOMERS_ENABLED`.

**Rollback:** `AURA_SANDBOX_ENABLED=false`. The desktop Engineer keeps working.

Docs: new ADR-047 (037–046 are used on `main` or PR #282), `docs/deployment/railway-sandboxes.md`,
updates to `desktop-engineer-customers.md`, the gap register, status files and `CLAUDE.md`.

## Verification

Baselines are run before the first change.

- `aura_backend`: `pytest`; `TESTING=True python -m unittest discover -p "test_*.py"`; `pytest tests/architecture -q`
- `aura_sandbox_broker`: `npm ci`; `npx tsc --noEmit`; `npm test`
- `aura_client`: `dart run build_runner build --delete-conflicting-outputs`; `flutter analyze`; `flutter test`

"Built" is not "proven": only the committed live report proves it.

### Manual test (Phase 7)

1. Staff account, no paired desktop: Engineer defaults to cloud and asks for cloud consent once.
2. Declining consent runs nothing.
3. Clone your repository, edit, run tests, push: approval is asked for the clone and the push,
   never for edits or commands inside the workspace.
4. Wait for the turn to end, ask again: the files and uncommitted changes are still there.
5. `curl https://example.com` is refused; `pip install` of a package works.
6. With a paired desktop: default is desktop; switch a new conversation to cloud; the choice is
   fixed after the first file change.
7. Desktop offline: an error appears and nothing runs in the cloud.
8. Open a third workspace: the oldest of yours is removed and the reply says which.
9. Pro test account: hours show and count up; with the allowance set to 0, a clear refusal.
10. Free account: no cloud option.
11. Delete a test account: its sandbox and checkpoint are gone from the Railway project.
12. Arabic: control, consent sheet and errors read right to left.
13. An older app build: desktop still works; cloud without consent says to update.

## Risks and open items

- Railway's CLI labels sandboxes experimental, and the SDK says it is beta.
- Checkpoint storage price is undocumented (S7).
- PR #282 changes `docker_sandbox.py`, `coding.py`, `workspaces.py`, `host_git.py`, settings and
  container wiring; whoever merges second resolves conflicts. It also hardens host git against
  repository hooks, which `main` does not do today.
- A turn that follows quickly waits for the previous checkpoint to finish.

## Out of scope

Moving the ADR-036 execution tools into the sandbox; admin console screens; shared team
workspaces; paid overage; changes to the VPS Docker stack; merging PR #282.
