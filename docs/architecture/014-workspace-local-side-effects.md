# ADR-014 — Confirmation gates the workspace boundary, not work inside it

**Status:** Accepted · **Date:** 2026-09-08

**Amends:** ADR-006 (rules 3 and 4). Read that first.

## Context

ADR-006 established that a side-effecting tool cannot run in a tainted context without user
confirmation, and that confidential or system-level side effects always confirm. Those rules
close the SEC-02 chain: a poisoned document cannot silently cause a write.

Persistent workspaces (ADR-013) broke the economics of that, in a way that would have quietly
destroyed the protection rather than tripping it. Two independent rules were involved:

1. `ToolSpec.__post_init__` forces `requires_confirmation` on any side-effecting tool at
   `CONFIDENTIAL` or above. `run_command` is `SYSTEM`, so **every command confirmed, tainted or
   not**.
2. `ToolPolicy.evaluate` confirms a side-effecting call in a tainted context, and `run_command`
   carries `untrusted_content` — so the first command taints the turn and everything after it
   confirms too.

Per-turn, that was tolerable: a sandbox lived for one message. Across a workspace that lives for
days, with the engineer persona's `max_plan_iterations` of 8, it is dozens of prompts per task —
every one of them approving a write to a scratch directory that gets deleted.

A gate asked that often is a gate that gets clicked through. The failure mode is not "users are
annoyed"; it is that the prompt which *does* matter — the push, the pull request — arrives in a
stream of identical prompts the user has been trained to approve. Left alone, persistence would
have degraded the taint mechanism while every test still passed.

## Decision

Taint gates the **boundary**, not work inside the box.

`ToolSpec` gains `workspace_local`: the tool's effects are confined to an ephemeral agent
workspace, reaching no user data, no external service, and nothing that outlives the task. Such a
tool is exempt from both rules above — the constructor does not force confirmation on it, and the
policy allows it in a tainted context.

The exemption is justified by the confinement, not by convenience. Confirming a write to a
directory that is about to be deleted buys nothing; declining to ask preserves the meaning of the
prompts that remain.

**Marked** (effects stay inside): `write_file`, `edit_file`, `run_command`, `git_commit`,
`git_checkout_branch`, `open_workspace`.

**Deliberately not marked** (effects leave, or cannot be undone):

- `clone_repository` — reaches a real repository over the network.
- `git_push`, `github_commit_files`, `github_create_branch`,
  `github_create_draft_pull_request` — publish.
- `destroy_workspace` — destroys unpushed work, and unlike everything else in a workspace it
  cannot be undone by running the step again.
- Every personal-OS and knowledge write — the user's own data.

`untrusted_content` is unaffected and still applies to `run_command`: the two flags answer
different questions. `workspace_local` says the tool's *effects* are confined;
`untrusted_content` says its *output* is attacker-influenceable and must still taint the turn —
which is what keeps the gate armed for whatever the agent tries next outside the workspace.

## Why this is safe

The chain ADR-006 was written to break runs through tools that write to *user* state, and none of
those are workspace-local. A poisoned repository can now freely edit files in the container that
holds it — which it could already do, since it is the thing being edited — and still cannot reach
a push, a forge write, or the user's records without a human seeing it.

## Guardrails

Because this relaxes a security rule, three things hold it in place:

- `tests/architecture/test_workspace_local_tools.py` pins the exact set as an explicit
  allowlist. Adding a tool means editing that file, which is the point at which somebody has to
  decide whether the confinement claim is still true. Its fixture deliberately builds the
  manager-wired tool set, because an earlier version did not and a new `workspace_local` tool
  was silently outside the pin.
- `tests/security/test_prompt_injection.py::TestUntrustedContentTaint` is unchanged and must stay
  green.
- `tests/unit/test_workspace_local_policy.py` asserts the boundary in both directions, including
  that the exemption does not touch the denylist, the agent grant, the capability gate, or the
  sensitivity ceiling, and that an unmarked tool behaves exactly as it did before.

## Alternatives rejected

| | Why not |
| --- | --- |
| **Leave it; confirm everything** | The honest description is not "more secure" but "one prompt per step, ~dozens per task, until the user stops reading them". It degrades the gate it appears to strengthen. |
| **A blanket per-turn approval** ("approve this whole task") | Coarser and worse: it also pre-approves the push, which is the one call a human should see individually. |
| **Turn off `block_side_effects_when_tainted` for the engineer persona** | Achieves the same convenience by disabling the rule wholesale, including for the boundary-crossing tools. This decision narrows the exemption to the tools that provably cannot escape. |
| **Make `run_command` non-side-effecting** | Would work by lying in the spec. Every other consumer of `side_effecting` (audit detail, retry policy) would then be wrong too. |

## Acceptance criterion

A full multi-turn session — clone, edit, run tests, push — produces **exactly one** confirmation
prompt, at `git_push`. That number is the test of whether this decision achieved what it set out
to, and it is step 10 of the on-VPS verification.

## Amendment (2026-10-29): the exemption follows the routing, not the tool

The exemption's justification is the confinement: a scratch checkout destroyed on expiry. That
holds in the server-side sandbox. It does not hold when the same tool is routed to the user's own
machine through a paired `aura_hostd` (ADR-011), where `write_file` and `run_command` act on real
files as the user's OS account. The audit (F-01, CB-05) found the exemption applied unchanged
there, so injected content could run commands on the user's computer without a prompt.

Now:

- `ToolRegistry` asks whether the call is host-routed. It uses the same predicate
  `CompositeSandboxRunner` routes on, a live host-agent session for the user (`container._host_routed`).
  It passes the answer to `ToolPolicy.evaluate(host_routed=...)`.
- A host-routed `workspace_local` side effect in a **tainted** turn is held for confirmation
  (`side_effect_on_user_machine_in_tainted_context`), whatever `block_side_effects_when_tainted`
  says.
- An untainted host-routed call keeps the exemption. The user asked for the work, and the
  daemon's own command policy still gates untrusted folders.
- The daemon refuses writes under `.git/hooks/` and to `.git/config` in every workspace
  (`protectedGitWriteReason`). Either one turns a file write into code that git runs.

Pinned by `tests/unit/test_host_routed_policy.py` and `aura_hostd/test/task_handler_test.dart`.
The acceptance criterion above is unchanged for sandbox sessions.
