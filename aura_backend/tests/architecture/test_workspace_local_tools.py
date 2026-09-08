"""Fitness: exactly which tools are exempt from confirmation as `workspace_local`.

`workspace_local` relaxes a security rule. A tool carrying it skips the taint gate and the
confidential-side-effect gate (ADR-006's rule 3 and 4, amended by ADR-014), which is sound only
while the claim behind the flag holds: that the tool's effects are confined to an ephemeral
workspace destroyed on expiry, reaching no user data and nothing that outlives the task.

Nothing structural enforces that claim -- it is a judgement about what a tool does. So the set is
pinned here as an explicit allowlist, the same reasoning `test_untrusted_content_tags.py` gives
for its own list: adding a tool to it requires editing this file, which is the point at which
somebody has to think about whether the claim is still true.

The second test is the one that matters most. Every tool that crosses out of the workspace -- a
push, any forge write, any write to the user's own data -- must NOT be marked, because those are
precisely the calls the confirmation gate exists for.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.tools.builtin import default_tools
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.workspaces import WorkspaceManager

#: Effects confined to the disposable workspace: a scratch container and a checkout that the
#: reaper deletes. Confirming a write to a directory that is about to be destroyed buys nothing,
#: and asking for it dozens of times per task is what makes people approve without reading.
_EXPECTED_WORKSPACE_LOCAL = {
    "write_file",
    "edit_file",
    "run_command",
    "git_commit",
    "git_checkout_branch",
    "open_workspace",
}

#: Tools that leave the workspace. Each one reaches something that outlives the task -- a
#: repository on GitHub, the user's own records -- so each keeps the full confirmation path.
_MUST_NOT_BE_WORKSPACE_LOCAL = {
    "clone_repository",
    # Destroys a checkout including unpushed work, and cannot be undone by re-running the step.
    "destroy_workspace",
    "github_create_branch",
    "github_commit_files",
    "github_create_draft_pull_request",
    "fetch_url",
}


async def _github_client_resolver(user_id):
    return InMemoryGitHubClient()


@pytest.fixture
def specs() -> dict:
    """Every tool the engineer agent can get, including the ones that only exist once durable
    workspaces are wired.

    Building without a workspace manager -- the shape `default_tools` produces on its own --
    would leave `open_workspace` unregistered and quietly outside this pin, which is exactly the
    gap that lets a `workspace_local` tool ship unreviewed.
    """
    runner = InMemorySandboxRunner()
    manager = WorkspaceManager(runner=runner, store=InMemoryWorkspaceStore())
    tools = default_tools(
        allowed_http_hosts=("example.com",),
        sandbox_runner=runner,
        github_client_resolver=_github_client_resolver,
        github_default_repo="acme/widgets",
    )
    tools += build_sandbox_tools(
        runner, code_host_resolver=_github_client_resolver, workspace_manager=manager
    )
    return {t.spec.name: t.spec for t in tools}


def test_the_workspace_local_set_is_exactly_what_is_pinned(specs) -> None:
    actual = {name for name, spec in specs.items() if spec.workspace_local}

    assert actual == _EXPECTED_WORKSPACE_LOCAL, (
        "the workspace_local set changed. This flag exempts a tool from the taint and "
        "confidential-side-effect confirmation gates, so a tool joins it only if its effects are "
        "genuinely confined to an ephemeral workspace. Read ADR-014 before editing this list."
    )


def test_boundary_crossing_tools_are_never_workspace_local(specs) -> None:
    """The gate has to keep working where it counts: a poisoned repository must not be able to
    reach a push or a forge write without a human seeing it."""
    for name in _MUST_NOT_BE_WORKSPACE_LOCAL:
        if name not in specs:
            continue
        assert not specs[name].workspace_local, (
            f"{name!r} reaches outside the workspace and must stay confirmation-gated"
        )


def test_no_tool_outside_the_engineer_agent_is_workspace_local(specs) -> None:
    """A workspace only exists for the engineer agent. Any other tool claiming to be confined to
    one is claiming something that cannot be true."""
    for name, spec in specs.items():
        if spec.workspace_local:
            assert spec.required_capability == "engineer_agent", (
                f"{name!r} is workspace_local but is not gated on engineer_agent; there is no "
                "workspace for it to be local to"
            )


def test_workspace_local_tools_still_require_the_capability(specs) -> None:
    """Relaxing confirmation must not relax who can call these at all: without the
    engineer_agent grant they are not even offered."""
    for name in _EXPECTED_WORKSPACE_LOCAL:
        assert specs[name].required_capability == "engineer_agent"


def test_run_command_keeps_its_untrusted_content_tag(specs) -> None:
    """The two flags are independent and both still apply. `workspace_local` says the tool's
    *effects* are confined; `untrusted_content` says its *output* is attacker-influenceable and
    must still taint the turn -- which is what keeps the gate armed for whatever the agent tries
    next outside the workspace."""
    assert specs["run_command"].workspace_local
    assert "untrusted_content" in specs["run_command"].tags
