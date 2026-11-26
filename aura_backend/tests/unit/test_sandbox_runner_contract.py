"""What every server-side ``SandboxRunner`` must do, run against two of them.

``memory`` is the substrate the tool and workspace tests use; ``cloud`` is the Railway runner over
``_fake_sandbox_broker`` (real HTTP requests and wire format, real commands in a temporary folder).
A difference between them is a bug in one: the tools above the port cannot tell them apart.

The second half covers the optional repository-transfer capability, which replaces
``workspace_path`` for a runner whose files are not on this host.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from aura_core.adapters.coding_fakes import InMemorySandboxRunner, InMemoryTransferSandboxRunner
from aura_core.adapters.host_agent import (
    CompositeSandboxRunner,
    HostAgentRegistry,
    HostAgentSandboxRunner,
)
from aura_core.adapters.memory_stores import InMemoryCloudSandboxStore
from aura_core.adapters.railway_sandbox import RailwaySandboxRunner
from aura_core.ports.coding import (
    FileWriteOp,
    RepositoryTransferRunner,
    SandboxHandle,
    SandboxRunner,
)

from ._fake_sandbox_broker import BROKER_TOKEN, BROKER_URL, FakeSandboxBroker


def _cloud(broker: FakeSandboxBroker) -> RailwaySandboxRunner:
    return RailwaySandboxRunner(
        broker_url=BROKER_URL, token=BROKER_TOKEN, store=InMemoryCloudSandboxStore(),
        transport=broker,
    )


@pytest.fixture(params=["memory", "cloud"])
async def runner(request):
    if request.param == "memory":
        memory = InMemorySandboxRunner()
        yield memory
        for key in await memory.workspace_keys():
            await memory.destroy(SandboxHandle(task_id=key))
        return
    broker = FakeSandboxBroker()
    cloud = _cloud(broker)
    yield cloud
    await cloud.close()
    broker.cleanup()


@pytest.fixture
async def task(runner):
    handle = await runner.create_task()
    yield handle
    await runner.destroy(handle)


class TestProtocol:
    async def test_it_is_a_sandbox_runner(self, runner) -> None:
        assert isinstance(runner, SandboxRunner)

    async def test_create_task_accepts_the_routing_arguments_and_ignores_them(self, runner) -> None:
        handle = await runner.create_task(
            user_id=None, session_id="s1", isolate=True, workspace_root="C:\\code", origin="remote"
        )
        await runner.write_file(handle, "a.txt", "ok")
        assert await runner.read_file(handle, "a.txt") == "ok"
        await runner.destroy(handle)


class TestFiles:
    async def test_a_command_sees_a_written_file(self, runner, task) -> None:
        await runner.write_file(task, "src/deep/note.txt", "hello")
        result = await runner.run(task, ["cat", "src/deep/note.txt"], timeout_s=20)
        assert (result.exit_code, result.stdout) == (0, "hello")

    async def test_a_second_write_replaces_the_file(self, runner, task) -> None:
        await runner.write_file(task, "a.txt", "one")
        await runner.write_file(task, "a.txt", "two")
        assert await runner.read_file(task, "a.txt") == "two"

    async def test_text_comes_back_exactly(self, runner, task) -> None:
        text = "line one\r\nline two\nunicode: é ✓ \u2028 end"
        await runner.write_file(task, "crlf.txt", text)
        assert await runner.read_file(task, "crlf.txt") == text
        assert await runner.read_file_bytes(task, "crlf.txt") == text.encode("utf-8")

    async def test_binary_content_is_bytes_only(self, runner, task) -> None:
        made = await runner.run(task, ["sh", "-c", "printf 'a\\000b' > blob.bin"], timeout_s=20)
        assert made.exit_code == 0
        assert await runner.read_file_bytes(task, "blob.bin") == b"a\x00b"
        with pytest.raises(Exception, match="binary"):
            await runner.read_file(task, "blob.bin")

    async def test_a_missing_file_is_named(self, runner, task) -> None:
        with pytest.raises(Exception, match="missing.txt is not a file"):
            await runner.read_file(task, "missing.txt")

    @pytest.mark.parametrize("path", ["../outside.txt", "a/../../outside.txt"])
    async def test_a_path_cannot_leave_the_workspace(self, runner, task, path) -> None:
        with pytest.raises(Exception, match="escapes"):
            await runner.write_file(task, path, "x")
        with pytest.raises(Exception, match="escapes"):
            await runner.read_file(task, path)

    async def test_a_leading_slash_stays_inside_the_workspace(self, runner, task) -> None:
        await runner.write_file(task, "/rooted.txt", "x")
        assert await runner.read_file(task, "rooted.txt") == "x"

    async def test_tasks_do_not_share_files(self, runner, task) -> None:
        other = await runner.create_task()
        await runner.write_file(task, "mine.txt", "x")
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(other, "mine.txt")
        await runner.destroy(other)


class TestWriteFiles:
    async def test_writes_and_edits_in_one_batch(self, runner, task) -> None:
        await runner.write_file(task, "old.py", "value = 1\n")
        await runner.write_files(task, [
            FileWriteOp(path="old.py", find="value = 1", replace="value = 2"),
            FileWriteOp(path="pkg/new.py", content="print('new')\n"),
        ])
        assert await runner.read_file(task, "old.py") == "value = 2\n"
        assert await runner.read_file(task, "pkg/new.py") == "print('new')\n"

    @pytest.mark.parametrize("bad, message", [
        (FileWriteOp(path="old.py", find="absent", replace="x"), "exactly one"),
        (FileWriteOp(path="nothing.py", find="a", replace="b"), "does not exist"),
        (FileWriteOp(path="../escape.py", content="x"), "escapes"),
        (FileWriteOp(path="old.py"), "either content, or both"),
    ])
    async def test_one_bad_operation_writes_nothing(self, runner, task, bad, message) -> None:
        await runner.write_file(task, "old.py", "value = 1\n")
        with pytest.raises(Exception, match=message):
            await runner.write_files(task, [FileWriteOp(path="first.py", content="x"), bad])
        assert await runner.read_file(task, "old.py") == "value = 1\n"
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(task, "first.py")

    async def test_a_write_that_fails_part_way_restores_the_earlier_files(
        self, runner, task
    ) -> None:
        await runner.write_file(task, "kept.txt", "before")
        await runner.write_file(task, "folder/inside.txt", "x")
        with pytest.raises(Exception, match="could not write folder"):
            await runner.write_files(task, [
                FileWriteOp(path="kept.txt", content="after"),
                FileWriteOp(path="added.txt", content="new"),
                FileWriteOp(path="folder", content="a file cannot replace a folder"),
            ])
        assert await runner.read_file(task, "kept.txt") == "before"
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(task, "added.txt")


class TestDeleteAndRename:
    async def test_delete_removes_one_file(self, runner, task) -> None:
        await runner.write_file(task, "dir/a.txt", "a")
        await runner.write_file(task, "dir/b.txt", "b")

        await runner.delete_file(task, "dir/a.txt")

        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(task, "dir/a.txt")
        assert await runner.read_file(task, "dir/b.txt") == "b"

    @pytest.mark.parametrize("path, message", [
        ("missing.txt", "does not exist"),
        ("dir", "is a directory"),
        ("../outside.txt", "escapes"),
        (".git/config", r"\.git"),
        ("dir/.GIT/x", r"\.git"),
    ])
    async def test_delete_refusals(self, runner, task, path, message) -> None:
        await runner.write_file(task, "dir/a.txt", "a")
        await runner.write_file(task, ".git/config", "[core]\n")

        with pytest.raises(Exception, match=message):
            await runner.delete_file(task, path)

        assert await runner.read_file(task, "dir/a.txt") == "a"
        assert await runner.read_file(task, ".git/config") == "[core]\n"

    async def test_rename_moves_and_makes_folders(self, runner, task) -> None:
        await runner.write_file(task, "old.txt", "x")

        await runner.rename_file(task, "old.txt", "new/deep/name.txt")

        assert await runner.read_file(task, "new/deep/name.txt") == "x"
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(task, "old.txt")

    async def test_a_folder_can_be_renamed(self, runner, task) -> None:
        await runner.write_file(task, "dir/a.txt", "a")

        await runner.rename_file(task, "dir", "moved")

        assert await runner.read_file(task, "moved/a.txt") == "a"

    @pytest.mark.parametrize("source, target, message", [
        ("missing.txt", "x.txt", "does not exist"),
        ("a.txt", "b.txt", "already exists"),
        ("a.txt", "../outside.txt", "escapes"),
        ("../outside.txt", "x.txt", "escapes"),
        ("a.txt", ".git/hooks/pre-push", r"\.git"),
        (".git/config", "x.txt", r"\.git"),
        ("dir", "dir/inside", "into itself"),
    ])
    async def test_rename_refusals(self, runner, task, source, target, message) -> None:
        for path in ("a.txt", "b.txt", "dir/c.txt", ".git/config"):
            await runner.write_file(task, path, path)

        with pytest.raises(Exception, match=message):
            await runner.rename_file(task, source, target)

        for path in ("a.txt", "b.txt", "dir/c.txt", ".git/config"):
            assert await runner.read_file(task, path) == path


class TestCommands:
    async def test_exit_code_and_both_streams(self, runner, task) -> None:
        result = await runner.run(
            task, ["sh", "-c", "echo out; echo err >&2; exit 3"], timeout_s=20
        )
        assert (result.exit_code, result.stdout.strip(), result.stderr.strip()) == (3, "out", "err")

    async def test_arguments_are_not_reinterpreted(self, runner, task) -> None:
        result = await runner.run(
            task, ["sh", "-c", 'printf "%s" "$1"', "x", "$(echo injected); 'q'"], timeout_s=20
        )
        assert result.stdout == "$(echo injected); 'q'"

    async def test_a_timeout_reports_124(self, runner, task) -> None:
        result = await runner.run(task, ["sh", "-c", "sleep 8"], timeout_s=1)
        assert result.exit_code == 124
        assert "timed out" in result.stderr

    async def test_the_composite_streams_it_with_one_final_done(self, runner) -> None:
        composite = CompositeSandboxRunner(
            host_runner=HostAgentSandboxRunner(HostAgentRegistry()), fallback=runner
        )
        handle = await composite.create_task()
        events = [
            event async for event in composite.run_streaming(
                handle, ["sh", "-c", "echo streamed; exit 2"], timeout_s=20
            )
        ]
        assert [e.kind for e in events].count("done") == 1
        assert events[-1].kind == "done" and events[-1].exit_code == 2
        assert "".join(e.data for e in events if e.kind == "stdout").strip() == "streamed"
        await composite.destroy(handle)


class TestEphemeral:
    async def test_a_destroyed_task_is_unusable(self, runner) -> None:
        handle = await runner.create_task()
        await runner.write_file(handle, "a.txt", "x")
        await runner.destroy(handle)
        with pytest.raises(Exception, match="unknown sandbox"):
            await runner.run(handle, ["true"], timeout_s=5)

    async def test_destroying_twice_is_harmless(self, runner) -> None:
        handle = await runner.create_task()
        await runner.destroy(handle)
        await runner.destroy(handle)


class TestDurableWorkspaces:
    async def test_files_survive_a_pause(self, runner) -> None:
        handle = await runner.open_workspace("k1")
        await runner.write_file(handle, "work.txt", "half done")
        await runner.pause_workspace(handle)
        again = await runner.open_workspace("k1")
        assert await runner.read_file(again, "work.txt") == "half done"
        await runner.destroy_workspace(again)

    async def test_two_keys_do_not_share_files(self, runner) -> None:
        one = await runner.open_workspace("k1")
        two = await runner.open_workspace("k2")
        await runner.write_file(one, "secret.txt", "x")
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(two, "secret.txt")
        await runner.destroy_workspace(one)
        await runner.destroy_workspace(two)

    async def test_destroy_removes_the_files_and_the_key(self, runner) -> None:
        handle = await runner.open_workspace("k1")
        await runner.write_file(handle, "work.txt", "x")
        await runner.pause_workspace(handle)
        await runner.destroy_workspace(handle)
        assert "k1" not in await runner.workspace_keys()
        fresh = await runner.open_workspace("k1")
        with pytest.raises(Exception, match="not a file"):
            await runner.read_file(fresh, "work.txt")
        await runner.destroy_workspace(fresh)

    async def test_an_opened_key_is_listed(self, runner) -> None:
        await runner.open_workspace("k1")
        assert "k1" in await runner.workspace_keys()
        await runner.destroy_workspace(SandboxHandle(task_id="k1"))

    async def test_disk_usage_grows_and_is_known_while_paused(self, runner) -> None:
        handle = await runner.open_workspace("k1")
        await runner.write_file(handle, "big.txt", "x" * (1024 * 1024))
        assert await runner.workspace_disk_usage_mb(handle) >= 0.9
        await runner.pause_workspace(handle)
        assert await runner.workspace_disk_usage_mb(handle) >= 0.9
        await runner.destroy_workspace(handle)

    async def test_stats_only_while_running(self, runner) -> None:
        handle = await runner.open_workspace("k1")
        await runner.write_file(handle, "a.txt", "x")
        stats = await runner.workspace_stats(handle)
        assert stats is not None and stats["started_at"]
        assert stats["cpu_percent"] is None and stats["memory_mb"] is None
        await runner.pause_workspace(handle)
        assert await runner.workspace_stats(handle) is None
        await runner.destroy_workspace(handle)


# -- repository transfer ------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
         "-c", "core.autocrlf=false", *args],
        cwd=cwd, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


@pytest.fixture
def checkout(tmp_path) -> Path:
    """A clone with a remote-tracking branch, as the gateway would hand it over."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    (seed / "README.md").write_bytes(b"# Widgets\n")
    (seed / "src").mkdir()
    (seed / "src" / "app.py").write_bytes(b"print('v1')\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-q", "-m", "first")
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(seed), str(clone))
    return clone


@pytest.fixture(params=["memory", "cloud"])
async def transfer(request):
    if request.param == "memory":
        runner = InMemoryTransferSandboxRunner()
        broker = None
    else:
        broker = FakeSandboxBroker()
        runner = _cloud(broker)
    handle = await runner.open_workspace("repo-ws")
    yield runner, handle
    await runner.destroy_workspace(handle)
    if broker is not None:
        await runner.close()
        broker.cleanup()


_COMMIT = (
    "cd widgets && git checkout -q -b feature/x && echo change >> README.md && "
    "git -c user.name=A -c user.email=a@example.com commit -qam change && git rev-parse HEAD"
)


class TestRepositoryTransfer:
    async def test_the_capability_replaces_the_host_path(self, transfer) -> None:
        runner, handle = transfer
        assert isinstance(runner, RepositoryTransferRunner)
        with pytest.raises(Exception, match="no path on this server"):
            await runner.workspace_path(handle)

    async def test_an_imported_checkout_is_a_working_repository(self, transfer, checkout) -> None:
        runner, handle = transfer
        await runner.import_repository(handle, checkout, "widgets")
        assert await runner.read_file(handle, "widgets/src/app.py") == "print('v1')\n"
        log = await runner.run(
            handle, ["git", "-C", "widgets", "log", "--format=%s"], timeout_s=30
        )
        assert log.stdout.strip() == "first"
        left = await runner.run(handle, ["ls", "-A"], timeout_s=30)
        assert left.stdout.split() == ["widgets"]

    async def test_a_folder_in_use_is_not_overwritten(self, transfer, checkout) -> None:
        runner, handle = transfer
        await runner.write_file(handle, "widgets/mine.txt", "x")
        with pytest.raises(Exception, match="not empty"):
            await runner.import_repository(handle, checkout, "widgets")
        assert await runner.read_file(handle, "widgets/mine.txt") == "x"

    @pytest.mark.parametrize("destination", ["../escape", ".", ""])
    async def test_the_destination_stays_inside_the_workspace(
        self, transfer, checkout, destination
    ) -> None:
        runner, handle = transfer
        with pytest.raises(Exception, match="workspace"):
            await runner.import_repository(handle, checkout, destination)

    async def test_new_commits_come_back_as_a_bundle(self, transfer, checkout, tmp_path) -> None:
        runner, handle = transfer
        await runner.import_repository(handle, checkout, "widgets")
        made = await runner.run(handle, ["sh", "-c", _COMMIT], timeout_s=60)
        assert made.exit_code == 0, made.stderr
        out = tmp_path / "out.bundle"

        export = await runner.export_branch(handle, "widgets", "feature/x", out)

        assert export.tip == made.stdout.strip()
        assert export.bundle == out
        target = tmp_path / "target"
        _git(tmp_path, "clone", "-q", str(checkout), str(target))
        _git(target, "fetch", "-q", str(out), "refs/heads/feature/x")
        assert _git(target, "rev-parse", "FETCH_HEAD") == export.tip
        left = await runner.run(handle, ["ls", "-A"], timeout_s=30)
        assert left.stdout.split() == ["widgets"]

    async def test_a_branch_with_nothing_new_has_no_bundle(
        self, transfer, checkout, tmp_path
    ) -> None:
        runner, handle = transfer
        await runner.import_repository(handle, checkout, "widgets")

        export = await runner.export_branch(handle, "widgets", "main", tmp_path / "none.bundle")

        assert export.bundle is None
        assert len(export.tip) == 40
        assert not (tmp_path / "none.bundle").exists()

    async def test_an_unknown_branch_is_refused(self, transfer, checkout, tmp_path) -> None:
        runner, handle = transfer
        await runner.import_repository(handle, checkout, "widgets")
        with pytest.raises(Exception, match="no branch"):
            await runner.export_branch(handle, "widgets", "nope", tmp_path / "x.bundle")
