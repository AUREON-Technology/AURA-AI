"""Workspace snapshots and rollback: ``workspace_snapshot``, ``list_workspace_snapshots``,
``workspace_restore`` (ADR-039).

Real ``git`` in a real directory: ``InMemorySandboxRunner`` runs commands for real, so a snapshot
here is an actual commit object under ``refs/aura/snapshots`` and a restore actually rewrites the
files. Everything is driven through a real ``ToolRegistry`` with the real policy, so the
confirmation a restore needs is the production one, not a stub's.

Not covered, because it needs a machine this suite does not have: the same scripts on a paired
desktop through ``aura_hostd`` (the daemon's own command policy decides whether ``sh -c`` with
these git commands runs unattended), and inside the Docker sandbox image.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner
from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain.errors import ToolExecutionError
from aura_core.domain.identifiers import PlanId, StepId, ToolCallId, UserId
from aura_core.domain.messages import ToolCall
from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
from aura_core.kernel import TurnRequest
from aura_core.ports.coding import TOOL_CALL_CONFIRMED
from aura_core.ports.runtime import ToolContext
from aura_core.tools.coding import build_sandbox_tools
from aura_core.tools.policy import ToolPolicy
from aura_core.tools.registry import ToolRegistry

ALICE = UserId("alice")
CAPS = ("engineer_agent",)


class Workspace:
    """One engineer workspace: a real directory, a real registry, and a few helpers."""

    def __init__(self, *, autonomy: str = "standard", allow_autonomous: bool = False, tainted: bool = False) -> None:
        self.runner = InMemorySandboxRunner()
        self.registry = ToolRegistry(policy=ToolPolicy(allow_autonomous=allow_autonomous))
        self.registry.register_all(build_sandbox_tools(self.runner))
        # The registry's own instances: one per-turn sandbox is shared by every call in a context.
        self.tools = self.registry._tools
        self.context = ToolContext(user_id=ALICE, correlation_id="corr-1", deadline_s=120.0,
                                   agent_id="engineer", autonomy=autonomy, tainted=tainted)

    async def call(self, name: str, *, confirmed: bool = False, **arguments):
        return await self.registry.execute(
            ToolCall(id=ToolCallId.new(), name=name, arguments=arguments), self.context,
            capabilities=CAPS, confirmed=confirmed)

    async def sh(self, command: str) -> str:
        result, report = await self.call("run_command", command=command, confirmed=True)
        assert result.ok, result.content
        return result.content

    @property
    def root(self) -> Path:
        # The registry and the standalone tools share this context's per-turn sandbox.
        (handle,) = self.runner._scratch_dirs
        return self.runner._scratch_dirs[handle]

    async def init_repo(self) -> None:
        await self.sh("git init -q -b main && git config user.email t@example.com && git config user.name T")
        await self.sh("printf 'one\\n' > a.txt && printf 'keep\\n' > b.txt && mkdir -p src && printf 'x=1\\n' > src/m.py")
        await self.sh("git add -A && git commit -q -m initial")

    async def state(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for path in sorted(self.root.rglob("*")):
            if path.is_file() and ".git" not in path.relative_to(self.root).parts:
                out[path.relative_to(self.root).as_posix()] = path.read_text()
        return out

    async def head(self) -> str:
        return (await self.sh("git rev-parse HEAD")).strip().splitlines()[-1]


async def _id(ws: Workspace, label: str) -> str:
    result, _ = await ws.call("workspace_snapshot", label=label, confirmed=True)
    assert result.ok, result.content
    return next(w for w in result.content.split() if w.startswith("snap-"))


# --------------------------------------------------------------------------------------
# Taking a snapshot changes nothing
# --------------------------------------------------------------------------------------


class TestSnapshot:
    async def test_it_changes_no_file_no_index_no_head_and_no_branch(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        await ws.sh("printf 'two\\n' >> a.txt && printf 'new\\n' > untracked.txt && git add b.txt")
        before = (await ws.state(), await ws.sh("git status --porcelain"), await ws.head(),
                  await ws.sh("git branch -a && git stash list && git log --oneline"))
        snapshot_id = await _id(ws, "before the experiment")
        after = (await ws.state(), await ws.sh("git status --porcelain"), await ws.head(),
                 await ws.sh("git branch -a && git stash list && git log --oneline"))
        assert before == after
        assert snapshot_id.startswith("snap-") and len(snapshot_id) == len("snap-") + 12

    async def test_it_needs_no_confirmation_and_is_confined_to_the_workspace(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        result, report = await ws.call("workspace_snapshot", label="x")
        assert result.ok and not report.needs_confirmation
        assert ws.registry.spec("workspace_snapshot").workspace_local is True

    async def test_the_snapshot_holds_untracked_files_and_ignores_ignored_ones(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        await ws.sh("printf 'node_modules/\\n' > .gitignore && git add .gitignore && git commit -q -m ignore")
        await ws.sh("printf 'new\\n' > fresh.txt && mkdir -p node_modules && printf 'dep\\n' > node_modules/x.js")
        snapshot_id = await _id(ws, "with untracked")
        listing = await ws.sh(f"git ls-tree -r --name-only refs/aura/snapshots/{snapshot_id}")
        assert "fresh.txt" in listing and "node_modules" not in listing

    async def test_a_label_with_shell_syntax_is_just_text(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        snapshot_id = await _id(ws, "$(touch pwned); `id` \"quoted\" 'single'\nsecond line")
        assert not (ws.root / "pwned").exists()
        listing, _ = await ws.call("list_workspace_snapshots")
        assert snapshot_id in listing.content and "$(touch pwned)" in listing.content
        assert "\n" not in next(l for l in listing.content.splitlines() if snapshot_id in l).split("  ", 2)[2]

    async def test_a_repository_with_no_commit_yet_can_be_snapshotted_and_restored(self) -> None:
        ws = Workspace()
        await ws.sh("git init -q -b main && printf 'v1\\n' > f.txt")
        snapshot_id = await _id(ws, "pre-commit")
        await ws.sh("printf 'v2\\n' > f.txt && printf 'z\\n' > g.txt")
        result, _ = await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        assert result.ok, result.content
        assert await ws.state() == {"f.txt": "v1\n"}

    async def test_outside_a_git_repository_the_error_says_what_to_do(self) -> None:
        ws = Workspace()
        await ws.sh("true")
        result, _ = await ws.call("workspace_snapshot", label="x")
        assert not result.ok and "not a git repository" in result.content

    async def test_an_empty_label_is_refused(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        result, _ = await ws.call("workspace_snapshot", label="   ")
        assert not result.ok and "label" in result.content

    async def test_only_the_newest_snapshots_are_kept(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        ids = []
        for i in range(23):
            await ws.sh(f"printf '{i}\\n' > counter.txt")
            ids.append(await _id(ws, f"n{i}"))
        listing, _ = await ws.call("list_workspace_snapshots")
        listed = [l.split()[1] for l in listing.content.splitlines()]
        assert len(listed) == 20
        assert ids[-1] in listed, "the newest is never the one pruned"
        assert ids[0] not in listed


# --------------------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------------------


class TestList:
    async def test_empty_and_then_newest_first_with_labels(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        empty, _ = await ws.call("list_workspace_snapshots")
        assert "No snapshots yet" in empty.content
        first = await _id(ws, "first state")
        await ws.sh("printf 'changed\\n' > a.txt")
        second = await _id(ws, "second state")
        listing, report = await ws.call("list_workspace_snapshots")
        lines = listing.content.splitlines()
        assert not report.needs_confirmation
        assert lines[0].startswith(f"- {second}") and lines[0].endswith("second state")
        assert lines[1].startswith(f"- {first}") and lines[1].endswith("first state")


# --------------------------------------------------------------------------------------
# Restoring
# --------------------------------------------------------------------------------------


class TestRestore:
    async def _messy(self, ws: Workspace) -> str:
        """A snapshot, then a modified tracked file, a deleted tracked file and new untracked ones."""
        await ws.init_repo()
        await ws.sh("printf 'draft\\n' > wip.txt")  # untracked *at snapshot time*
        snapshot_id = await _id(ws, "known good")
        await ws.sh("printf 'broken\\n' > a.txt && rm b.txt && printf 'oops\\n' > oops.txt "
                    "&& mkdir -p added/deep && printf 'q\\n' > added/deep/q.txt && printf 'changed\\n' > wip.txt")
        return snapshot_id

    async def test_it_always_asks_first_and_nothing_changes_until_approved(self) -> None:
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        messy = await ws.state()
        held, report = await ws.call("workspace_restore", snapshot_id=snapshot_id)
        assert report.needs_confirmation and not held.ok
        assert await ws.state() == messy, "an unapproved restore touched the workspace"

    async def test_an_approved_restore_returns_every_file_to_the_snapshot(self) -> None:
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        head = await ws.head()
        result, _ = await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        assert result.ok, result.content
        assert await ws.state() == {
            "a.txt": "one\n", "b.txt": "keep\n", "src/m.py": "x=1\n", "wip.txt": "draft\n",
        }
        assert await ws.head() == head, "HEAD does not move"
        assert "main" in await ws.sh("git symbolic-ref --short HEAD")

    async def test_files_added_since_are_removed_including_directories(self) -> None:
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        assert not (ws.root / "oops.txt").exists() and not (ws.root / "added").exists()

    async def test_ignored_files_are_left_alone(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        await ws.sh("printf 'node_modules/\\n.env\\n' > .gitignore && git add -A && git commit -q -m ig")
        await ws.sh("mkdir -p node_modules && printf 'dep\\n' > node_modules/x.js && printf 'S=1\\n' > .env")
        snapshot_id = await _id(ws, "s")
        await ws.sh("printf 'dirty\\n' >> a.txt")
        await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        assert (ws.root / "node_modules" / "x.js").read_text() == "dep\n"
        assert (ws.root / ".env").read_text() == "S=1\n"

    async def test_the_executable_bit_comes_back(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        await ws.sh("printf '#!/bin/sh\\n' > run.sh && chmod +x run.sh && git add -A && git commit -q -m exe")
        snapshot_id = await _id(ws, "with exe")
        await ws.sh("chmod -x run.sh && printf 'x\\n' >> run.sh")
        await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        assert (ws.root / "run.sh").stat().st_mode & stat.S_IXUSR

    async def test_the_state_it_replaced_is_saved_so_the_restore_can_be_undone(self) -> None:
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        messy = await ws.state()
        result, _ = await ws.call("workspace_restore", snapshot_id=snapshot_id, confirmed=True)
        ids = [w.rstrip(".,;") for w in result.content.split() if w.startswith("snap-")]
        undo_id = next(i for i in ids if i != snapshot_id)
        assert await ws.state() != messy
        again, _ = await ws.call("workspace_restore", snapshot_id=undo_id, confirmed=True)
        assert again.ok, again.content
        assert await ws.state() == messy, "undoing the restore did not bring the messy state back"

    async def test_a_snapshot_that_does_not_exist_changes_nothing(self) -> None:
        ws = Workspace()
        await ws.init_repo()
        await ws.sh("printf 'mine\\n' > a.txt")
        mine = await ws.state()
        missing, _ = await ws.call("workspace_restore", snapshot_id="snap-000000000000", confirmed=True)
        assert not missing.ok and "no snapshot" in missing.content
        assert await ws.state() == mine, "a failed restore must leave the workspace as it was"

    @pytest.mark.parametrize("bad", ["main", "snap-xyz", "../../etc/passwd", "snap-0123456789ab; rm -rf .",
                                     "refs/heads/main", "SNAP-0123456789AB"])
    async def test_only_a_real_snapshot_id_is_accepted(self, bad: str) -> None:
        ws = Workspace()
        await ws.init_repo()
        result, _ = await ws.call("workspace_restore", snapshot_id=bad, confirmed=True)
        assert not result.ok and "not a snapshot id" in result.content
        assert await ws.state() == {"a.txt": "one\n", "b.txt": "keep\n", "src/m.py": "x=1\n"}

    async def test_the_handler_itself_refuses_to_discard_work_without_a_confirmation(self) -> None:
        """The second lock: reached without the registry's approval, a dirty restore still refuses."""
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        messy = await ws.state()
        assert TOOL_CALL_CONFIRMED.get() is False
        with pytest.raises(ToolExecutionError, match="confirmation"):
            await ws.tools["workspace_restore"].execute({"snapshot_id": snapshot_id}, ws.context)
        assert await ws.state() == messy

    async def test_the_confirmation_preview_shows_what_would_be_lost(self) -> None:
        ws = Workspace()
        snapshot_id = await self._messy(ws)
        _, report = await ws.call("workspace_restore", snapshot_id=snapshot_id)
        assert report.needs_confirmation and report.diff_preview
        assert "oops.txt" in report.diff_preview and "a.txt" in report.diff_preview
        assert "undone" in report.diff_preview

    async def test_a_workspace_inside_a_larger_repository_is_refused_before_anything_is_touched(self, tmp_path) -> None:
        """Restoring rewrites the whole repository's index and tree; a granted subfolder of a bigger
        repo must not have files outside it rewritten."""
        outer = tmp_path / "outer"
        (outer / "inner").mkdir(parents=True)
        (outer / "outside.txt").write_text("original\n")
        os_run = __import__("subprocess").run
        os_run(["git", "init", "-q", "-b", "main"], cwd=outer, check=True)
        os_run(["git", "-c", "user.email=t@e", "-c", "user.name=T", "add", "-A"], cwd=outer, check=True)
        os_run(["git", "-c", "user.email=t@e", "-c", "user.name=T", "commit", "-q", "-m", "x"], cwd=outer, check=True)

        class InnerRunner(InMemorySandboxRunner):
            def _scratch(self, handle):  # the workspace is the subfolder, as on a desktop grant
                return outer / "inner"

        runner = InnerRunner()
        runner._scratch_dirs["t"] = outer / "inner"
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register_all(build_sandbox_tools(runner))
        context = ToolContext(user_id=ALICE, correlation_id="c", deadline_s=60.0, agent_id="engineer")
        # Make the per-turn cache hand back a handle for the pre-made directory.
        handle = await runner.create_task()
        runner._scratch_dirs[handle.task_id] = outer / "inner"
        (outer / "outside.txt").write_text("edited after commit\n")

        for name, args, confirmed in (("workspace_snapshot", {"label": "x"}, False),
                                      ("workspace_restore", {"snapshot_id": "snap-0123456789ab"}, True)):
            result, _ = await registry.execute(
                ToolCall(id=ToolCallId.new(), name=name, arguments=args), context,
                capabilities=CAPS, confirmed=confirmed)
            assert not result.ok and "larger repository" in result.content, (name, result.content)
        assert (outer / "outside.txt").read_text() == "edited after commit\n"


# --------------------------------------------------------------------------------------
# Gating
# --------------------------------------------------------------------------------------


class TestGating:
    async def test_without_the_engineer_capability_the_tools_do_not_exist_for_the_caller(self) -> None:
        ws = Workspace()
        offered = {s.name for s in ws.registry.available(capabilities=())}
        granted = {s.name for s in ws.registry.available(capabilities=CAPS)}
        names = {"workspace_snapshot", "list_workspace_snapshots", "workspace_restore"}
        assert not names & offered and names <= granted
        await ws.init_repo()
        result, _ = await ws.registry.execute(
            ToolCall(id=ToolCallId.new(), name="workspace_snapshot", arguments={"label": "x"}),
            ws.context, capabilities=())
        assert not result.ok and "missing_capability" in result.content

    @pytest.mark.parametrize("tainted", [False, True])
    @pytest.mark.parametrize("autonomy", ["standard", "strict", "autonomous"])
    async def test_a_restore_is_held_for_confirmation_under_every_profile_and_taint(self, autonomy, tainted) -> None:
        ws = Workspace(autonomy=autonomy, allow_autonomous=True, tainted=tainted)
        await ws.init_repo()
        snapshot_id = await _id(ws, "s") if autonomy != "strict" else None
        if snapshot_id is None:  # strict holds the snapshot itself; approve it to get an id
            result, _ = await ws.call("workspace_snapshot", label="s", confirmed=True)
            snapshot_id = next(w for w in result.content.split() if w.startswith("snap-"))
        result, report = await ws.call("workspace_restore", snapshot_id=snapshot_id)
        assert report.needs_confirmation and not result.ok

    def test_the_engineer_persona_holds_all_three_and_no_other_persona_does(self) -> None:
        agents = build_default_agent_registry()
        names = {"workspace_snapshot", "list_workspace_snapshots", "workspace_restore"}
        assert all(agents.get("engineer").permits_tool(n) for n in names)
        for spec in agents:
            if spec.id != "engineer" and spec.allowed_tools is not None:
                assert not any(spec.permits_tool(n) for n in names), spec.id


# --------------------------------------------------------------------------------------
# Through a real kernel turn
# --------------------------------------------------------------------------------------


class _Steps:
    """A planner that plans an ordered list of (tool, arguments) steps."""

    def __init__(self, steps) -> None:
        self._steps = steps

    def needs_plan(self, message, *, available_tools=(), force=None) -> bool:
        return True

    @staticmethod
    def goal_from_message(message, *, criteria=()) -> Goal:
        return Goal(description=message.strip(), success_criteria=tuple(criteria))

    async def plan(self, goal, *, available_tools=(), max_steps=None) -> Plan:
        steps = []
        for index, (tool, arguments) in enumerate(self._steps):
            steps.append(PlanStep(
                id=StepId(f"s{index}"), kind=StepKind.TOOL, description=tool, tool=tool,
                arguments=arguments, depends_on=(StepId(f"s{index - 1}"),) if index else ()))
        return Plan(id=PlanId.new(), goal=goal, steps=tuple(steps))

    def can_replan(self, plan, *, limit=None) -> bool:
        return False


class TestThroughTheKernel:
    async def test_snapshot_runs_and_restore_is_held_in_a_real_turn(self, container) -> None:
        await container.startup()
        # The default test container has no server-side sandbox (that needs Docker); give its
        # registry the real tools over the real-directory runner.
        runner = InMemorySandboxRunner()
        for tool in build_sandbox_tools(runner):
            container.tools.register(tool, overwrite=True)
        container.kernel._deps.planner = _Steps([
            ("run_command", {"command": "git init -q -b main && printf 'a\\n' > f.txt"}),
            ("workspace_snapshot", {"label": "start"}),
            ("write_file", {"path": "f.txt", "content": "b\n"}),
            ("workspace_restore", {"snapshot_id": "snap-0123456789ab"}),
        ])
        result = await container.kernel.run(
            TurnRequest(message="try something and roll back", user_id=ALICE, use_tools=True, capabilities=CAPS))
        assert result.tool_calls[:3] == ("run_command", "workspace_snapshot", "write_file")
        assert result.needs_confirmation == ("workspace_restore",)
        assert [p["tool"] for p in result.pending_tool_calls] == ["workspace_restore"]
        # What ran, really ran: the snapshot exists as a ref in the turn's checkout, the file was
        # rewritten after it, and the held restore has not touched either.
        (root,) = runner._scratch_dirs.values()
        refs = __import__("subprocess").run(
            ["git", "for-each-ref", "refs/aura/snapshots"], cwd=root, capture_output=True, text=True).stdout
        assert refs.count("refs/aura/snapshots/snap-") == 1
        assert (root / "f.txt").read_text() == "b\n"
