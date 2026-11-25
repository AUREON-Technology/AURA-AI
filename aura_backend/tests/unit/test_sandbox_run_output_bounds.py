"""One-shot command containers (``DockerSandboxRunner.run``) bound the output they hold.

``run_command`` reaches the Docker sandbox through this method. A command a model chose, in a
repository an attacker may control, can print without limit; two things then went wrong:

* ``container.logs()`` read the whole log into the gateway process, so a command that printed
  without limit grew the gateway's memory (the daemon is now asked for the tail only; the broker
  refuses a followed/streamed log, so ``stream=True`` is not an option); and
* a command that overran its timeout was killed and *its output was thrown away*, so the one
  thing a failed or hung test run can tell the agent (how far it got) never arrived.
"""

from __future__ import annotations

import pytest

from aura_core.adapters.docker_sandbox import DockerSandboxRunner


class _Container:
    def __init__(self, *, stdout=b"", stderr=b"", wait_error=None, status_code=0) -> None:
        self._stdout, self._stderr = stdout, stderr
        self._wait_error, self._status_code = wait_error, status_code
        self.killed = self.removed = False
        self.log_calls: list[dict] = []

    def wait(self, timeout=None):
        if self._wait_error is not None:
            raise self._wait_error
        return {"StatusCode": self._status_code}

    def logs(self, *, stdout=True, stderr=True, tail="all", **kwargs):
        self.log_calls.append({"stdout": stdout, "stderr": stderr, "tail": tail, **kwargs})
        if stdout and not stderr:
            data = self._stdout
        elif stderr and not stdout:
            data = self._stderr
        else:
            data = self._stdout + self._stderr
        if tail != "all":  # the daemon returns only the last `tail` lines
            data = b"".join(data.splitlines(keepends=True)[-int(tail):])
        return data

    def kill(self):
        self.killed = True

    def remove(self, force=False):
        self.removed = True


class _Client:
    def __init__(self, container) -> None:
        self._container, self.run_kwargs, self.containers = container, None, self

    def run(self, **kwargs):
        self.run_kwargs = kwargs
        return self._container

    def list(self, **kwargs):  # pragma: no cover
        return []


class _ReadTimeout(Exception):
    pass


@pytest.fixture
def runner(tmp_path) -> DockerSandboxRunner:
    return DockerSandboxRunner(
        image="aura-sandbox:latest", network="sandbox_egress", host_root=str(tmp_path)
    )


class TestTheLogIsReadBounded:
    async def test_the_daemon_is_asked_for_the_tail_only(self, runner) -> None:
        container = _Container(stdout=b"out", stderr=b"err")
        runner._client = _Client(container)
        handle = await runner.create_task()

        await runner.run(handle, ["true"], timeout_s=30)

        assert container.log_calls
        for call in container.log_calls:
            assert isinstance(call["tail"], int) and call["tail"] > 0
            assert not call.get("stream") and not call.get("follow")

    async def test_a_command_that_printed_many_lines_is_marked_truncated(self, runner) -> None:
        runner._client = _Client(_Container(stdout=b"line\n" * 20_000))
        handle = await runner.create_task()

        result = await runner.run(handle, ["yes"], timeout_s=30)

        assert result.stdout.count("\n") == 5_000
        assert "truncated" in result.stderr

    async def test_the_create_request_carries_no_setting_the_broker_does_not_allow(
        self, runner
    ) -> None:
        """The sandbox broker refuses any container setting it has not listed, so a one-shot
        container must not start sending one (a log driver option was refused with a 403)."""
        runner._client = _Client(_Container())
        handle = await runner.create_task()

        await runner.run(handle, ["true"], timeout_s=30)

        assert "log_config" not in runner._client.run_kwargs


class TestOutputHeldInProcessIsBounded:
    async def test_huge_stdout_keeps_the_tail_and_says_it_was_cut(self, runner) -> None:
        cap = 200_000
        body = b"A" * 500_000 + b"THE-SUMMARY-LINE"
        runner._client = _Client(_Container(stdout=body))
        handle = await runner.create_task()

        result = await runner.run(handle, ["yes"], timeout_s=30)

        assert len(result.stdout.encode()) <= cap
        assert result.stdout.endswith("THE-SUMMARY-LINE")
        assert "truncated" in result.stderr

    async def test_huge_stderr_is_bounded_too(self, runner) -> None:
        runner._client = _Client(_Container(stderr=b"E" * 500_000))
        handle = await runner.create_task()

        result = await runner.run(handle, ["yes"], timeout_s=30)

        assert len(result.stderr.encode()) < 200_000 + 500
        assert "truncated" in result.stderr

    async def test_small_output_is_returned_whole_and_unmarked(self, runner) -> None:
        runner._client = _Client(_Container(stdout=b"hello\n", stderr=b"warn\n", status_code=3))
        handle = await runner.create_task()

        result = await runner.run(handle, ["x"], timeout_s=30)

        assert (result.stdout, result.stderr, result.exit_code) == ("hello\n", "warn\n", 3)

    async def test_a_cut_in_the_middle_of_a_character_does_not_break_decoding(self, runner) -> None:
        runner._client = _Client(_Container(stdout=("é" * 150_000).encode()))
        handle = await runner.create_task()

        result = await runner.run(handle, ["x"], timeout_s=30)

        assert result.stdout.strip("�").strip("é") == ""


class TestATimedOutCommandKeepsItsOutput:
    async def test_partial_output_is_returned_with_the_timeout_note(self, runner) -> None:
        container = _Container(
            stdout=b"collected 40 items\ntest_a PASSED\n",
            stderr=b"warning: slow\n",
            wait_error=_ReadTimeout("read timed out"),
        )
        runner._client = _Client(container)
        handle = await runner.create_task()

        result = await runner.run(handle, ["pytest"], timeout_s=5)

        assert result.exit_code == 124
        assert "collected 40 items" in result.stdout
        assert "command timed out after 5s" in result.stderr
        assert "warning: slow" in result.stderr
        assert container.killed and container.removed

    async def test_a_failure_to_read_the_log_still_reports_the_timeout(self, runner) -> None:
        class Broken(_Container):
            def logs(self, **kwargs):
                raise RuntimeError("daemon went away")

        container = Broken(wait_error=_ReadTimeout("read timed out"))
        runner._client = _Client(container)
        handle = await runner.create_task()

        result = await runner.run(handle, ["sleep", "99"], timeout_s=5)

        assert result.exit_code == 124
        assert "timed out" in result.stderr
        assert container.killed and container.removed
