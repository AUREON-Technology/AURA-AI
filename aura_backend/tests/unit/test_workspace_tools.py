"""The engineer tools once durable workspaces are wired.

The property that matters is the one the whole feature exists for: the same repository resolves
to the same files in a *later turn*, which is modelled here as a second `ToolContext` with a
different correlation id -- exactly what a second message produces.

Everything runs on the real in-memory substrate: `InMemorySandboxRunner` really creates
directories and really runs commands, `HostGitService` really runs git. The only thing not
exercised is the container, which needs a daemon.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemoryGitHubClient, InMemorySandboxRunner
from aura_core.adapters.host_git import HostGitService
from aura_core.adapters.memory_stores import InMemoryWorkspaceStore
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import UserId
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.workspaces import WorkspaceManager

_ALICE = UserId("alice")


class _Clock:
    def __init__(self) -> None:
        self._now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs) -> None:
        self._now += timedelta(**kwargs)


def _ctx(correlation_id: str) -> ToolContext:
    return ToolContext(user_id=_ALICE, correlation_id=correlation_id, deadline_s=60.0)


async def _resolver(user_id):
    return InMemoryGitHubClient()


@pytest.fixture
def clock() -> _Clock:
    return _Clock()


@pytest.fixture
def runner() -> InMemorySandboxRunner:
    return InMemorySandboxRunner()


@pytest.fixture
def manager(runner, clock) -> WorkspaceManager:
    return WorkspaceManager(
        runner=runner, store=InMemoryWorkspaceStore(), clock=clock, min_free_memory_mb=0
    )


@pytest.fixture
def tools(runner, manager) -> dict:
    built = build_sandbox_tools(
        runner,
        code_host_resolver=_resolver,
        workspace_manager=manager,
        host_git=HostGitService(timeout_s=60.0),
    )
    return {t.spec.name: t for t in built}


class TestPersistenceAcrossTurns:
    async def test_files_survive_into_a_later_turn(self, tools) -> None:
        """The whole point. A different correlation id is a different message; under the old
        per-turn cache this file was gone."""
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("turn-1"))
        await tools["write_file"].execute(
            {"path": "notes.txt", "content": "half-finished"}, _ctx("turn-1")
        )

        readback = await tools["run_command"].execute(
            {"command": "cat notes.txt"}, _ctx("turn-2-different-message")
        )

        assert "half-finished" in readback

    async def test_a_second_repository_does_not_see_the_first(self, tools) -> None:
        """Isolation by construction: separate containers over separate directories, which is
        why a hostile repository cannot read another one's checkout."""
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))
        await tools["write_file"].execute({"path": "secret.txt", "content": "x"}, _ctx("t1"))

        await tools["open_workspace"].execute({"repo": "acme/gadgets"}, _ctx("t2"))
        listing = await tools["run_command"].execute({"command": "ls -A"}, _ctx("t2"))

        assert "secret.txt" not in listing

    async def test_work_without_a_repository_still_works(self, tools) -> None:
        """"Write me a script and run it" names no repository and should not be made to invent
        one; it gets the per-user scratch workspace."""
        await tools["write_file"].execute(
            {"path": "hello.sh", "content": "echo hi"}, _ctx("t1")
        )

        result = await tools["run_command"].execute({"command": "sh hello.sh"}, _ctx("t1"))

        assert "hi" in result

    async def test_every_call_records_activity(self, tools, manager, clock) -> None:
        """The 72-hour TTL measures inactivity, so a workspace in use must never look idle --
        including on a read, because reading a file is use."""
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))
        clock.advance(hours=71)

        await tools["run_command"].execute({"command": "true"}, _ctx("t2"))
        clock.advance(hours=71)

        assert await manager.expired() == []


class TestWorkspaceManagementTools:
    async def test_list_workspaces_reports_what_is_open(self, tools) -> None:
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))

        listing = await tools["list_workspaces"].execute({}, _ctx("t2"))

        assert "acme/widgets" in listing

    async def test_list_workspaces_is_honest_when_there_are_none(self, tools) -> None:
        assert "no workspaces" in (await tools["list_workspaces"].execute({}, _ctx("t1"))).lower()

    async def test_destroy_removes_the_workspace_and_its_files(self, tools, manager) -> None:
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))
        await tools["write_file"].execute({"path": "a.txt", "content": "x"}, _ctx("t1"))

        await tools["destroy_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))

        assert await manager.get(_ALICE, "acme/widgets") is None

    async def test_destroying_an_unknown_workspace_says_so(self, tools) -> None:
        result = await tools["destroy_workspace"].execute({"repo": "acme/nope"}, _ctx("t1"))

        assert "No workspace" in result


class TestGitPush:
    async def _checkout(self, tools, runner, manager, git, ctx, repo="acme/widgets"):
        """A real git checkout inside the workspace, as clone_repository would leave one.

        Opened through the tool rather than the manager so the turn is bound to the repository
        the same way a real clone binds it -- pushing relies on that binding to know what it is
        pushing to.
        """
        await tools["open_workspace"].execute({"repo": repo}, ctx)
        opened = await manager.open(_ALICE, repo)
        root = Path(await runner.workspace_path(opened.handle)) / repo.split("/")[-1]
        root.mkdir(parents=True)
        await git._checked(["init", "-b", "main"], cwd=root)
        (root / "f.txt").write_text("one\n")
        await git.commit(root, message="first")
        return root

    async def test_push_refuses_the_default_branch(self, tools, runner, manager) -> None:
        """Not left to the forge's branch protection: not every repository has any, and "the
        agent pushed straight to main" is not something to find out afterwards."""
        git = HostGitService(timeout_s=60.0)
        ctx = _ctx("t1")
        await self._checkout(tools, runner, manager, git, ctx)

        with pytest.raises(ToolExecutionError, match="refusing to push"):
            await tools["git_push"].execute({"branch": "main"}, ctx)

    async def test_push_needs_a_repository(self, tools) -> None:
        await tools["write_file"].execute({"path": "a.txt", "content": "x"}, _ctx("t1"))

        with pytest.raises(ToolExecutionError, match="no repository"):
            await tools["git_push"].execute({"branch": "feature/x"}, _ctx("t1"))

    async def test_push_needs_an_actual_checkout(self, tools) -> None:
        await tools["open_workspace"].execute({"repo": "acme/widgets"}, _ctx("t1"))

        with pytest.raises(ToolExecutionError, match="not a git checkout"):
            await tools["git_push"].execute({"branch": "feature/x"}, _ctx("t1"))

    def test_push_is_confirmation_gated(self, tools) -> None:
        """CONFIDENTIAL + side_effecting forces it, and it is deliberately not workspace_local:
        this is the boundary ADR-012 keeps the confirmation gate for."""
        spec = tools["git_push"].spec

        assert spec.requires_confirmation
        assert not spec.workspace_local

    def test_intra_workspace_tools_are_not_gated(self, tools) -> None:
        for name in ("write_file", "edit_file", "run_command", "git_commit"):
            assert not tools[name].spec.requires_confirmation
            assert tools[name].spec.workspace_local


class TestDegradedDeployments:
    async def test_workspace_tools_are_absent_without_a_manager(self, runner) -> None:
        """A deployment with no workspace manager keeps the old per-turn sandbox and simply is
        not offered the tools that would lie to the model about persistence."""
        built = {t.spec.name for t in build_sandbox_tools(runner)}

        assert "open_workspace" not in built
        assert "run_command" in built

    async def test_push_is_absent_without_host_git(self, runner, manager) -> None:
        built = {
            t.spec.name
            for t in build_sandbox_tools(runner, workspace_manager=manager)
        }

        assert "git_push" not in built
