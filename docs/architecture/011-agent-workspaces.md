# ADR-011 — Persistent engineer-agent workspaces, with git on the host side

**Status:** Accepted · **Date:** 2026-09-08

## Context

The engineer persona could read a repository but not really work on one. Two limits, both
deliberate at the time:

**Sandboxes died with the turn.** `_SandboxSessionCache` keyed a container by
`ToolContext.correlation_id`. A second message is a new correlation id, so "clone it and fix the
failing test" started from an empty container every time. The port said so outright: no implicit
cleanup, "because a runner that reaps sandboxes on a timer cannot tell a slow legitimate task
from an abandoned one."

**`clone_repository` was not a clone.** It copied file contents through GitHub's REST API, capped
at 400 files and 8 MB, producing no `.git`, no history, no branches. An agent could read code and
could not bisect, blame, diff against a base, or push. That cap existed for a real reason: a
genuine clone needs a credential, and a workspace container runs model-chosen shell commands
against a repository an attacker may control.

A third problem blocked either fix from working at all. `DockerSandboxRunner` created its scratch
directory with `tempfile.mkdtemp()` *inside the gateway container*, then handed that path to the
host daemon as a bind-mount source. The daemon resolves bind sources on the host, where the path
does not exist, so Docker silently created an empty host directory — `write_file` wrote on one
side and `run_command` read the other, with nothing raising. It had never been hit because
`AURA_SANDBOX_ENABLED` defaults false and the test suite wires an in-memory runner that has no
second filesystem to disagree with.

The deployment constrains the answer. One Contabo VPS, ~16.25 GB of documented service budget
against a 16 GB floor, Ollama alone reserving 10 GB and 6 of 8 cores.

## Decision

### 1. A workspace is a checkout, not a running process

One durable workspace per `(user, repository)`, in three states: **hot** (container running),
**warm** (container stopped, checkout intact), **expired** (reaped). Between turns the container
is stopped rather than kept alive or deleted.

This is what makes persistence affordable. A stopped container holds no memory, so peak RAM is
`max_running × memory_limit` — about 1 GB on the defaults — regardless of how many workspaces
exist. Keeping four resident would be ~2 GB competing with the model server for the same pages.
`pause` is therefore load-bearing, not an optimisation.

Scoped per repository rather than per user because it is also the isolation boundary: two
repositories mean two containers over two filesystems, so a hostile repository cannot read the
other's checkout. Per *conversation* was the alternative and would re-clone for every new thread
about the same repository — the cost this exists to remove.

### 2. Admission control refuses rather than degrades

`open_workspace` reads `MemAvailable` (which inside a container reports the host) and declines
when the host cannot afford another container. Starting it anyway hands the choice to the OOM
killer, and the largest process here is the model server — "start it and see" means an unrelated
user's chat dies to free room for someone's build. A refusal is something the agent can report.
An unreadable `/proc/meminfo` admits: a missing metric is not evidence of pressure.

Beyond `max_running`, the least recently used workspace is paused; beyond `max_workspaces`, the
least recently used is deleted. Pausing is recoverable and still reported; deleting a checkout is
not recoverable by waiting, so it is never silent.

### 3. Git runs on the host, not in the container

`HostGitService` runs real `git` in the gateway against the workspace directory. The container
gets a complete checkout — history, branches, tags — and never the token. Three rules make that
true rather than intended:

- **Never in argv.** `/proc/<pid>/cmdline` is world-readable, so `https://x-access-token:TOKEN@…`
  leaks to every process on the host for the life of the clone. It goes through `GIT_ASKPASS`.
- **Never in the checkout.** `git clone` records its URL in `.git/config`, which is inside the
  bind-mounted directory. The remote is written credential-free from the start rather than
  rewritten after — rewriting leaves a window a command in the container can read —
  and an assertion fails loudly if a credential appears anyway.
- **Never in a shared environment.** The subprocess gets a minimal environment, not the
  gateway's, which holds every other secret this process has.

`AURA_SANDBOX_IN_CONTAINER_GIT` is the opt-out for operators who prefer git inside the container
and accept that a model-chosen command can then read the credential. Off by default.

### 4. Expiry is measured from last use

A workspace is deleted after 72 hours (configurable) since `last_active_at`, which every tool
call updates. Measured from last use rather than creation, so one used daily for a month is not
stale and one created an hour ago and abandoned is on its way out. A Celery beat task sweeps
every 30 minutes: pause what is idle, delete what has expired, reconcile drift in both
directions. It is idempotent, survives a single wedged container, and records each expiry in the
existing `EngineerRun` history — a workspace that is simply gone one day, with no record of why,
is worse than one that expired.

### 5. The container is hardened for a longer life

A repo-built, digest-pinned image rather than a runtime pull — which lets `IMAGES` come off
docker-socket-proxy's allowlist entirely, leaving only container operations. Non-root uid,
`cap_drop: ALL`, read-only rootfs with tmpfs for `/tmp` and `HOME`, pids and fd limits,
`memswap_limit` pinned to `mem_limit` so exceeding memory fails rather than swapping against
Postgres. `sandbox_egress` is `internal: true` with an allowlisting proxy as the only route out.

## Alternatives rejected

| | Why not |
| --- | --- |
| **Token inside the container** (a git credential helper there) | Simplest mental model, and puts a live credential next to shell commands a model chose, in a container that now lives for days. Available behind `AURA_SANDBOX_IN_CONTAINER_GIT` for operators who accept that; not the default. |
| **Keep workspaces running between turns** | Removes the pause/resume machinery and costs `max_workspaces × memory_limit` permanently. On this host that is memory the inference tier needs. |
| **Per-user workspace holding many repositories** | Fewer containers, lower RAM — and one poisoned repository then shares a filesystem with every other repository that user is working on. |
| **Named Docker volumes instead of a host bind path** | The daemon cannot hand a volume name to a sibling container's bind source, and the socket proxy has `VOLUMES` off. A host path mounted into the gateway at the identical path is what makes one path mean one directory. |
| **No egress at all** | Safe and unusable: no `pip install`, no `npm ci`, no fetching the repository. The allowlisting proxy is the version that can do the job. |

## Consequences

A persistent checkout is a longer-lived target than a per-turn one; the mitigations above shrink
what a compromised workspace reaches, and the 72-hour TTL bounds the window. Everything ships off
by default (`AURA_SANDBOX_ENABLED=false`, `AURA_ENGINEER_AGENT_ROLES=` empty), and the compose
services sit behind the `sandbox` profile.

The confirmation consequence is large enough to be its own decision: see ADR-012.
