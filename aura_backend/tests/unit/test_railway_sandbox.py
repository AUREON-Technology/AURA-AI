"""The cloud sandbox runner's own promises, over ``_fake_sandbox_broker``.

What the shared contract cannot show because only this runner has a network between it and its
files: commands are never sent twice, an answer that never came is "unknown" rather than success or
failure, a workspace that could not be saved is never destroyed, a vanished sandbox is reported
instead of silently replaced, and several web workers sharing one store do not run two copies of a
workspace.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import pytest

from aura_core.adapters import railway_sandbox
from aura_core.adapters.memory_stores import InMemoryCloudSandboxStore
from aura_core.adapters.railway_sandbox import RailwaySandboxRunner, SandboxOutcomeUnknown
from aura_core.ports.coding import FileWriteOp, SandboxHandle

from ._fake_sandbox_broker import BROKER_TOKEN, BROKER_URL, FakeSandboxBroker, Fault


@pytest.fixture
def broker():
    fake = FakeSandboxBroker()
    yield fake
    fake.cleanup()


@pytest.fixture
def store() -> InMemoryCloudSandboxStore:
    return InMemoryCloudSandboxStore()


@pytest.fixture
async def make(broker, store):
    """Build runners over one broker and one store: each stands for a web worker."""
    built: list[RailwaySandboxRunner] = []

    def _make(**options) -> RailwaySandboxRunner:
        options.setdefault("save_wait_s", 5.0)
        options.setdefault("poll_s", 0.01)
        runner = RailwaySandboxRunner(
            broker_url=BROKER_URL, token=BROKER_TOKEN, store=store, transport=broker, **options
        )
        built.append(runner)
        return runner

    yield _make
    for runner in built:
        await runner.close()


@pytest.fixture
def runner(make) -> RailwaySandboxRunner:
    return make()


async def _ready(runner, key: str = "ws1") -> SandboxHandle:
    """A workspace with a booted sandbox holding one file."""
    handle = await runner.open_workspace(key)
    await runner.write_file(handle, "work.txt", "v1")
    return handle


def _started(*more: dict) -> list[dict]:
    return [{"type": "started", "command_id": "0" * 32}, *more]


class TestNothingIsBootedUntilNeeded:
    async def test_opening_a_workspace_creates_no_sandbox(self, runner, broker) -> None:
        await runner.open_workspace("ws1")
        assert broker.count("create") == 0

    async def test_one_sandbox_serves_later_calls(self, runner, broker) -> None:
        handle = await _ready(runner)
        await runner.run(handle, ["true"], timeout_s=10)
        assert await runner.read_file(handle, "work.txt") == "v1"
        assert broker.count("create") == 1

    async def test_pausing_and_destroying_an_unbooted_workspace_boots_nothing(
        self, runner, broker
    ) -> None:
        handle = await runner.open_workspace("ws1")
        await runner.pause_workspace(handle)
        await runner.destroy_workspace(handle)
        assert broker.requests == []

    async def test_a_boot_survives_the_caller_giving_up(self, runner, broker) -> None:
        """A tool call has a short deadline; a boot can be longer. The next call must find the
        sandbox rather than start, and abandon, another one."""
        release = broker.hold("create")
        handle = await runner.open_workspace("ws1")
        first = asyncio.create_task(runner.write_file(handle, "a.txt", "x"))
        await asyncio.wait_for(broker.entered["create"].wait(), 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()

        await runner.write_file(handle, "a.txt", "y")

        assert broker.count("create") == 1
        assert len(broker.sandboxes) == 1

    async def test_two_workers_booting_at_once_keep_one_sandbox(self, make, broker) -> None:
        one, two = make(), make()
        release = broker.hold("create")
        handle = await one.open_workspace("ws1")
        writes = [
            asyncio.create_task(one.write_file(handle, "one.txt", "1")),
            asyncio.create_task(two.write_file(handle, "two.txt", "2")),
        ]
        async with asyncio.timeout(5):
            while broker.count("create") < 2:
                await asyncio.sleep(0.01)
        release.set()
        await asyncio.gather(*writes)

        assert len(broker.sandboxes) == 1, "the sandbox that lost the race must be destroyed"
        assert await one.read_file(handle, "two.txt") == "2"
        assert await two.read_file(handle, "one.txt") == "1"


class TestACommandIsNeverSentTwice:
    @pytest.mark.parametrize("fault", [
        Fault(status=502, code="provider_error"),
        Fault(status=409, code="sandbox_busy"),
        Fault(lose_answer=True),
        Fault(unreachable=True),
        Fault(lines=_started(), cut=True),
        Fault(lines=_started({"type": "stdout", "data": "half"})),
        Fault(lines=_started({"type": "error", "error": "exit_unknown"})),
        Fault(lines=_started({"type": "error", "error": "provider_error"})),
    ])
    async def test_whatever_goes_wrong(self, runner, broker, fault) -> None:
        handle = await _ready(runner)
        attempts = 0
        original = broker.handle_async_request

        async def counted(request):
            nonlocal attempts
            attempts += request.url.path.endswith("/exec")
            return await original(request)

        broker.handle_async_request = counted
        broker.fail("exec", fault)

        with pytest.raises(Exception):
            await runner.run(handle, ["sh", "-c", "echo once >> log.txt"], timeout_s=10)

        assert attempts == 1

    @pytest.mark.parametrize("fault", [
        Fault(lose_answer=True),
        Fault(lines=_started(), cut=True),
        Fault(lines=_started({"type": "stdout", "data": "half"})),
        Fault(lines=_started({"type": "error", "error": "exit_unknown"})),
        Fault(status=502, code="provider_error"),
    ])
    async def test_no_known_end_means_unknown(self, runner, broker, fault) -> None:
        handle = await _ready(runner)
        broker.fail("exec", fault)

        with pytest.raises(SandboxOutcomeUnknown, match="unknown"):
            await runner.run(handle, ["true"], timeout_s=10)

    @pytest.mark.parametrize("fault", [
        Fault(unreachable=True),
        Fault(status=409, code="sandbox_busy"),
        Fault(status=401, code="unauthorized"),
    ])
    async def test_a_refused_command_did_not_run(self, runner, broker, fault) -> None:
        handle = await _ready(runner)
        broker.fail("exec", fault)

        with pytest.raises(Exception, match="did not run") as raised:
            await runner.run(handle, ["true"], timeout_s=10)

        assert not isinstance(raised.value, SandboxOutcomeUnknown)

    async def test_the_stream_keeps_what_arrived_and_says_unknown(self, runner, broker) -> None:
        handle = await _ready(runner)
        broker.fail("exec", Fault(lines=_started({"type": "stdout", "data": "half"}), cut=True))

        events = [e async for e in runner.run_streaming(handle, ["true"], timeout_s=10)]

        assert [e.kind for e in events] == ["stdout", "stderr", "done"]
        assert events[0].data == "half"
        assert "unknown" in events[1].data
        assert events[-1].exit_code != 0

    async def test_a_line_holding_a_unicode_line_separator_is_one_line(
        self, runner, broker
    ) -> None:
        handle = await _ready(runner)
        broker.fail("exec", Fault(lines=_started(
            {"type": "stdout", "data": "a b c\x0cd\x85e"},
            {"type": "exit", "exit_code": 0, "timed_out": False, "truncated": False},
        )))

        result = await runner.run(handle, ["true"], timeout_s=10)

        assert result.stdout == "a b c\x0cd\x85e"

    async def test_a_truncated_output_says_so(self, runner, broker) -> None:
        handle = await _ready(runner)
        broker.fail("exec", Fault(lines=_started(
            {"type": "stdout", "data": "x"},
            {"type": "exit", "exit_code": 0, "timed_out": False, "truncated": True},
        )))

        result = await runner.run(handle, ["true"], timeout_s=10)

        assert "truncated" in result.stderr

    @pytest.mark.parametrize("asked, sent", [(0.2, 1), (10.5, 11), (5000, 900)])
    async def test_the_timeout_is_a_whole_number_within_the_broker_limits(
        self, runner, broker, asked, sent
    ) -> None:
        handle = await _ready(runner)

        await runner.run(handle, ["true"], timeout_s=asked)

        assert json.loads(broker.requests[-1][2])["timeout_s"] == sent


class TestALostSandboxIsReported:
    async def test_a_vanished_sandbox_is_not_silently_replaced(self, runner, broker) -> None:
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        await runner.write_file(handle, "work.txt", "unsaved")
        broker.vanish(next(iter(broker.sandboxes)))

        with pytest.raises(Exception, match="since it was last saved"):
            await runner.run(handle, ["cat", "work.txt"], timeout_s=10)
        assert broker.count("exec") == 1

        again = await runner.run(handle, ["cat", "work.txt"], timeout_s=10)
        assert again.stdout == "v1"

    async def test_a_file_read_reports_it_too(self, runner, broker) -> None:
        handle = await _ready(runner)
        broker.vanish(next(iter(broker.sandboxes)))

        with pytest.raises(Exception, match="no longer exists"):
            await runner.read_file(handle, "work.txt")

    async def test_measuring_the_disk_does_not_hide_the_loss(self, runner, broker) -> None:
        """Every file write checks the quota first; that check must not swap in the older copy."""
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        await runner.write_file(handle, "work.txt", "unsaved")
        broker.vanish(next(iter(broker.sandboxes)))

        await runner.workspace_disk_usage_mb(handle)

        with pytest.raises(Exception, match="since it was last saved"):
            await runner.write_file(handle, "other.txt", "x")
        assert await runner.read_file(handle, "work.txt") == "v1"

    async def test_a_save_that_finds_the_sandbox_gone_tells_the_next_caller(
        self, runner, broker, store
    ) -> None:
        """A pause is housekeeping, often for somebody else's request: it frees the slot, and the
        workspace's own next call is the one told about the loss."""
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        await runner.write_file(handle, "work.txt", "unsaved")
        broker.vanish(next(iter(broker.sandboxes)))

        await runner.pause_workspace(handle)

        assert (await store.get("ws1")).sandbox_id is None
        with pytest.raises(Exception, match="since it was last saved"):
            await runner.read_file(handle, "work.txt")
        assert await runner.read_file(handle, "work.txt") == "v1"

    @pytest.mark.parametrize("code", ["fence_missing", "sandbox_unsafe", "sandbox_not_found"])
    async def test_a_sandbox_the_broker_discarded_is_forgotten(
        self, runner, broker, store, code
    ) -> None:
        handle = await _ready(runner)
        broker.fail("exec", Fault(lines=_started({"type": "error", "error": code})))

        with pytest.raises(Exception):
            await runner.run(handle, ["true"], timeout_s=10)

        assert (await store.get("ws1")).sandbox_id is None

    async def test_a_lost_checkpoint_is_not_replaced_by_an_empty_workspace(
        self, runner, broker
    ) -> None:
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        broker.checkpoints.clear()

        with pytest.raises(Exception, match="saved copy"):
            await runner.read_file(handle, "work.txt")
        assert broker.sandboxes == {}


class TestSaving:
    async def test_pause_saves_then_destroys_and_the_next_call_boots_from_the_save(
        self, runner, broker, store
    ) -> None:
        handle = await _ready(runner)

        await runner.pause_workspace(handle)

        assert broker.sandboxes == {}
        saved = (await store.get("ws1")).checkpoint_name
        assert list(broker.checkpoints) == [saved]
        routes = [name for name, _, _ in broker.requests]
        assert routes.index("checkpoint") < routes.index("destroy")
        assert await runner.read_file(handle, "work.txt") == "v1"
        assert broker.created[-1]["checkpoint"] == saved

    async def test_each_save_replaces_the_one_before(self, runner, broker) -> None:
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        first = list(broker.checkpoints)
        await runner.write_file(handle, "work.txt", "v2")

        await runner.pause_workspace(handle)

        assert len(broker.checkpoints) == 1
        assert list(broker.checkpoints) != first
        assert await runner.read_file(handle, "work.txt") == "v2"

    @pytest.mark.parametrize("fault", [
        Fault(), Fault(lose_answer=True), Fault(unreachable=True),
        Fault(status=409, code="sandbox_busy"),
    ])
    async def test_a_failed_save_never_destroys_the_sandbox(
        self, runner, broker, store, fault
    ) -> None:
        handle = await _ready(runner)
        broker.fail("checkpoint", fault)

        with pytest.raises(Exception, match="not saved"):
            await runner.pause_workspace(handle)

        assert len(broker.sandboxes) == 1
        assert broker.count("destroy") == 0
        record = await store.get("ws1")
        assert record.checkpoint_name is None and record.saving_until is None
        assert await runner.read_file(handle, "work.txt") == "v1"

    async def test_a_failed_destroy_keeps_the_save_and_the_sandbox_on_record(
        self, runner, broker, store
    ) -> None:
        handle = await _ready(runner)
        broker.fail("destroy")

        with pytest.raises(Exception):
            await runner.pause_workspace(handle)

        record = await store.get("ws1")
        assert record.checkpoint_name in broker.checkpoints
        assert record.sandbox_id in broker.sandboxes
        assert record.saving_until is None
        await runner.pause_workspace(handle)
        assert broker.sandboxes == {}

    async def test_a_call_made_during_a_save_waits_for_it(self, runner, broker) -> None:
        handle = await _ready(runner)
        release = broker.hold("checkpoint")
        pausing = asyncio.create_task(runner.pause_workspace(handle))
        await asyncio.wait_for(broker.entered["checkpoint"].wait(), 5)
        reading = asyncio.create_task(runner.read_file(handle, "work.txt"))
        await asyncio.sleep(0.05)
        assert not reading.done()

        release.set()

        assert await asyncio.wait_for(reading, 10) == "v1"
        await pausing

    async def test_another_worker_waits_for_the_save_too(self, make, broker) -> None:
        one, two = make(), make()
        handle = await _ready(one)
        release = broker.hold("checkpoint")
        pausing = asyncio.create_task(one.pause_workspace(handle))
        await asyncio.wait_for(broker.entered["checkpoint"].wait(), 5)
        running = asyncio.create_task(two.run(handle, ["cat", "work.txt"], timeout_s=10))
        await asyncio.sleep(0.05)
        assert broker.count("exec") == 0

        release.set()

        assert (await asyncio.wait_for(running, 10)).stdout == "v1"
        await pausing

    async def test_a_save_that_outlasts_the_wait_is_reported(self, make, broker) -> None:
        one, two = make(), make(save_wait_s=0.05)
        handle = await _ready(one)
        release = broker.hold("checkpoint")
        pausing = asyncio.create_task(one.pause_workspace(handle))
        await asyncio.wait_for(broker.entered["checkpoint"].wait(), 5)

        with pytest.raises(Exception, match="still being saved"):
            await two.read_file(handle, "work.txt")

        release.set()
        await pausing

    async def test_a_workspace_with_a_command_running_is_not_saved(self, make, broker) -> None:
        one, two = make(), make()
        handle = await _ready(one)
        release = broker.hold("exec")
        running = asyncio.create_task(one.run(handle, ["true"], timeout_s=10))
        await asyncio.wait_for(broker.entered["exec"].wait(), 5)

        with pytest.raises(Exception, match="still running"):
            await two.pause_workspace(handle)

        assert broker.count("checkpoint") == 0
        release.set()
        await running
        await two.pause_workspace(handle)
        assert broker.sandboxes == {}

    async def test_a_stream_abandoned_at_done_leaves_the_workspace_saveable(
        self, runner, broker
    ) -> None:
        """``run_command`` stops reading at ``done`` without closing the generator."""
        handle = await _ready(runner)
        async for event in runner.run_streaming(handle, ["true"], timeout_s=10):
            if event.kind == "done":
                break

        await runner.pause_workspace(handle)

        assert broker.sandboxes == {}

    async def test_a_save_survives_the_caller_giving_up(self, runner, broker, store) -> None:
        handle = await _ready(runner)
        release = broker.hold("checkpoint")
        pausing = asyncio.create_task(runner.pause_workspace(handle))
        await asyncio.wait_for(broker.entered["checkpoint"].wait(), 5)
        pausing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pausing
        release.set()
        await runner.close()

        record = await store.get("ws1")
        assert record.sandbox_id is None and record.checkpoint_name in broker.checkpoints
        assert broker.sandboxes == {}


class TestAnAnswerThatIsNotJson:
    @pytest.mark.parametrize("body", [b"<html>oops</html>", b"[]", b'{"bytes": "many"}'])
    async def test_an_unreadable_size_does_not_stop_or_wedge_a_save(
        self, runner, broker, store, body
    ) -> None:
        handle = await _ready(runner)
        broker.fail("usage", Fault(body=body))

        await runner.pause_workspace(handle)

        assert broker.sandboxes == {}
        assert (await store.get("ws1")).saving_until is None

    @pytest.mark.parametrize("body", [b"<html>oops</html>", b"[]", b'{"id": "../../x"}'])
    async def test_a_boot_without_a_usable_id_is_an_error(self, runner, broker, body) -> None:
        handle = await runner.open_workspace("ws1")
        broker.fail("create", Fault(body=body))

        with pytest.raises(Exception, match="without a sandbox id"):
            await runner.write_file(handle, "a.txt", "x")


class TestDestroy:
    async def test_it_removes_the_sandbox_the_checkpoint_and_the_record(
        self, runner, broker, store
    ) -> None:
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        await runner.read_file(handle, "work.txt")

        await runner.destroy_workspace(handle)

        assert broker.sandboxes == {} and broker.checkpoints == {}
        assert await store.get("ws1") is None

    @pytest.mark.parametrize("route", ["destroy", "delete_checkpoint"])
    async def test_a_failure_keeps_the_record_for_a_retry(
        self, runner, broker, store, route
    ) -> None:
        handle = await _ready(runner)
        await runner.pause_workspace(handle)
        await runner.read_file(handle, "work.txt")
        broker.fail(route)

        with pytest.raises(Exception):
            await runner.destroy_workspace(handle)
        assert await store.get("ws1") is not None

        await runner.destroy_workspace(handle)
        assert broker.sandboxes == {} and broker.checkpoints == {}
        assert await store.get("ws1") is None


class TestUsage:
    async def test_a_paused_workspace_reports_its_saved_size_without_booting(
        self, runner, broker
    ) -> None:
        handle = await runner.open_workspace("ws1")
        await runner.write_file(handle, "big.txt", "x" * 2048)
        await runner.pause_workspace(handle)
        created = broker.count("create")

        used = await runner.workspace_disk_usage_mb(handle)

        assert used == pytest.approx(2048 / (1024 * 1024))
        assert broker.count("create") == created

    async def test_an_unreadable_size_is_an_error_not_zero(self, runner, broker) -> None:
        handle = await _ready(runner)
        broker.fail("usage")

        with pytest.raises(Exception):
            await runner.workspace_disk_usage_mb(handle)


class TestFilePaths:
    @pytest.mark.parametrize("path", [
        "/workspace/app.py", "/home/aura/workspace/app.py", "./app.py", "sub/../app.py", "app.py",
    ])
    async def test_the_spellings_of_one_file(self, runner, path) -> None:
        handle = await runner.open_workspace("ws1")
        await runner.write_file(handle, path, "x")
        assert await runner.read_file(handle, "app.py") == "x"

    @pytest.mark.parametrize("path", ["", ".", "/", "/workspace", "a\x00b", "../x"])
    async def test_a_path_that_names_no_file_reaches_no_sandbox(
        self, runner, broker, path
    ) -> None:
        handle = await runner.open_workspace("ws1")
        with pytest.raises(Exception):
            await runner.write_file(handle, path, "x")
        assert broker.requests == []

    async def test_a_write_batch_whose_answer_is_lost_is_undone(self, runner, broker) -> None:
        handle = await _ready(runner)
        broker.fail("put", Fault(lose_answer=True))

        with pytest.raises(Exception, match="could not write work.txt"):
            await runner.write_files(handle, [FileWriteOp(path="work.txt", content="v2")])

        assert await runner.read_file(handle, "work.txt") == "v1"


class TestAnImportThatFails:
    async def test_no_half_checkout_is_left_and_a_retry_works(
        self, runner, broker, tmp_path, monkeypatch
    ) -> None:
        """A checkout cut off while unpacking would pass for the repository, with most files
        showing as deleted."""
        source = tmp_path / "checkout"
        (source / ".git").mkdir(parents=True)
        (source / ".git" / "HEAD").write_bytes(b"ref: refs/heads/main\n")
        for number in range(40):
            (source / f"file{number:02d}.bin").write_bytes(os.urandom(4096))
        whole = railway_sandbox._pack

        def cut_short(from_, archive, max_bytes) -> None:
            whole(from_, archive, max_bytes)
            data = archive.read_bytes()
            archive.write_bytes(data[: len(data) // 2])

        handle = await runner.open_workspace("ws1")
        monkeypatch.setattr(railway_sandbox, "_pack", cut_short)
        with pytest.raises(Exception, match="could not unpack"):
            await runner.import_repository(handle, source, "widgets")

        left = await runner.run(handle, ["ls", "-A"], timeout_s=30)
        assert left.stdout.split() == []

        monkeypatch.setattr(railway_sandbox, "_pack", whole)
        await runner.import_repository(handle, source, "widgets")
        assert await runner.read_file(handle, "widgets/.git/HEAD") == "ref: refs/heads/main\n"

    async def test_an_existing_empty_folder_is_filled(self, runner, tmp_path) -> None:
        source = tmp_path / "checkout"
        source.mkdir()
        (source / "a.txt").write_text("a", encoding="utf-8")
        handle = await runner.open_workspace("ws1")
        await runner.run(handle, ["mkdir", "-p", "nested/widgets"], timeout_s=30)

        await runner.import_repository(handle, source, "nested/widgets")

        assert await runner.read_file(handle, "nested/widgets/a.txt") == "a"
        left = await runner.run(handle, ["ls", "-A"], timeout_s=30)
        assert left.stdout.split() == ["nested"]


class TestTheBrokerSecret:
    async def test_a_refusal_never_repeats_the_token(self, broker, store, caplog) -> None:
        wrong = "wrong-token-0123456789abcdef0123456789abcd"
        runner = RailwaySandboxRunner(
            broker_url=BROKER_URL, token=wrong, store=store, transport=broker
        )
        handle = await runner.open_workspace("ws1")
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(Exception, match="unauthorized") as raised:
                await runner.write_file(handle, "a", "b")
        await runner.close()
        assert wrong not in str(raised.value)
        assert wrong not in caplog.text

    async def test_the_client_ignores_proxy_variables(self, runner) -> None:
        """The token and the user's code travel to the broker and nowhere else."""
        client = await runner._http()
        assert client.trust_env is False and client.follow_redirects is False
