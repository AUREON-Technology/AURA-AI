"""The Engineer's desktop execution path, proven against the real daemon (audit: "the actual
desktop execution path was not verified").

Every earlier test of ``write_file`` / ``edit_file`` / ``run_command`` / the git tools ran against
an in-memory ``SandboxRunner``; none touched the Dart daemon (``aura_hostd``) or the WebSocket
relay between it and the gateway, so none could show that the desktop path works or that its
containment holds. This file does, with nothing on the path replaced:

* the gateway is ``aura_service.app`` -- the production assembly -- on a real uvicorn server;
* the daemon is a real ``aura_hostd`` child process, paired through the real ``auth pair`` flow,
  holding the device token the gateway issued, started with a deliberately dirty environment;
* the transport is the daemon's own WebSocket client talking to uvicorn's WebSocket stack;
* the tools are the real registry's ``write_file``, ``edit_file``, ``write_files``,
  ``run_command`` and git tools, called through ``ToolRegistry.execute`` and (for one test)
  through ``AIKernel`` as an Engineer turn.

Every assertion is on what really happened -- bytes on disk, the process table, the daemon's own
HTTP control API -- not on a tool's own claim that it succeeded.

Gated: ``AURA_REAL_DESKTOP=1`` (``scripts/real_desktop_path.sh`` does the set-up). Without it
every test here skips, so the default ``pytest`` run needs no Dart toolchain.
``AURA_REAL_DESKTOP_SLOW=1`` additionally runs the five-minute approval-expiry check.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import signal
import stat
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

ENABLED = os.environ.get("AURA_REAL_DESKTOP") == "1"
SLOW = os.environ.get("AURA_REAL_DESKTOP_SLOW") == "1"

pytestmark = [
    pytest.mark.skipif(
        not ENABLED,
        reason="real desktop path: set AURA_REAL_DESKTOP=1 (scripts/real_desktop_path.sh) -- needs "
        "the Dart SDK and starts a real gateway and a real aura_hostd",
    ),
    pytest.mark.asyncio(loop_scope="module"),
]

if ENABLED:
    from tests.e2e import real_desktop_rig as R

    R.prepare_environment()


# -- the rig -----------------------------------------------------------------------------------


class Desk:
    """Everything a test needs to drive the real path."""

    def __init__(self, gateway, daemon, base: Path) -> None:
        self.gw = gateway
        self.daemon = daemon
        self.base = base
        self.container = gateway.container
        self.trusted: Path
        self.untrusted: Path
        self.outside: Path
        self.shim_log: Path

    # -- calling tools ---------------------------------------------------------------------

    def context(self, root: Path | str | None, *, corr: str | None = None, origin: str | None = None,
                deadline_s: float = 600.0):
        from aura_core.domain.identifiers import UserId
        from aura_core.ports.runtime import ToolContext
        from aura_core.security.role_capabilities import ENGINEER_AGENT

        metadata: dict[str, Any] = {}
        if root is not None:
            metadata["workspace_root"] = str(root)
        if origin:
            metadata["origin"] = origin
        return ToolContext(
            user_id=UserId(R.OPERATOR_UID), correlation_id=corr or uuid.uuid4().hex,
            deadline_s=deadline_s, capabilities=(ENGINEER_AGENT,), metadata=metadata,
        )

    async def tool(self, name: str, arguments: dict[str, Any], *, root: Path | str | None = None,
                   corr: str | None = None, origin: str | None = None, confirmed: bool = False,
                   timeout: float = 120.0):
        """One call through the real registry. Returns ``(result, report)``."""
        from aura_core.domain.identifiers import ToolCallId
        from aura_core.domain.messages import ToolCall
        from aura_core.security.role_capabilities import ENGINEER_AGENT

        call = ToolCall(id=ToolCallId(uuid.uuid4().hex), name=name, arguments=arguments)
        return await asyncio.wait_for(
            self.container.tools.execute(
                call, self.context(root if root is not None else self.trusted, corr=corr,
                                   origin=origin),
                capabilities=(ENGINEER_AGENT,), confirmed=confirmed,
            ),
            timeout=timeout,
        )

    async def run(self, command: str, *, root: Path | str | None = None, timeout_s: float = 30,
                  **kwargs):
        return await self.tool("run_command", {"command": command, "timeout_s": timeout_s},
                               root=root, **kwargs)

    # -- calling the daemon without the tool layer, to see its own error codes ----------------

    async def session(self):
        from aura_core.domain.identifiers import UserId

        session = await self.container.host_agent_registry.resolve(UserId(R.OPERATOR_UID))
        assert session is not None, "no daemon is connected"
        return session

    async def rpc(self, method: str, params: dict[str, Any], *, timeout_s: float = 60.0):
        """``(result, None)`` or ``(None, error_frame)`` -- the frame is what the daemon put on
        the wire, ``code`` included (the tool layer keeps only the message)."""
        from aura_core.adapters.docker_sandbox import SandboxError

        session = await self.session()
        mark = len(self.gw.tape)
        try:
            return await session.call(method, params, timeout_s=timeout_s), None
        except SandboxError as exc:
            errors = [f for f in self.gw.tape[mark:] if f.get("type") == "error"]
            return None, (errors[-1] if errors else {"message": str(exc), "code": None})

    async def task(self, root: Path | str, **extra: Any) -> str:
        result, error = await self.rpc("create_task", {"cwd": str(root), **extra})
        assert error is None, error
        return result["task_id"]

    # -- the daemon's human-facing side --------------------------------------------------------

    async def approvals(self) -> list[dict[str, Any]]:
        response = await self.daemon.control("GET", "/approvals")
        assert response.status_code == 200, response.text
        return [a for a in response.json()["approvals"] if a.get("status") == "pending"]

    async def decide(self, approved: bool, *, containing: str | None = None, timeout: float = 30.0):
        """Wait for a pending approval (optionally one whose command/paths mention
        ``containing``) and answer it as the human at the desktop would."""

        async def find():
            for item in await self.approvals():
                text = " ".join(str(item.get(k) or "") for k in ("command", "paths", "diff_preview"))
                if containing is None or containing in text:
                    return item
            return None

        item = await R.wait_until(find, timeout=timeout, what=f"a pending approval for {containing!r}")
        response = await self.daemon.control(
            "POST", f"/approvals/{item['id']}/respond", json_body={"approved": approved}
        )
        assert response.status_code == 200, response.text
        return item

    async def offline(self) -> bool:
        return not await self.gw.online()

    async def status(self) -> dict[str, Any]:
        return (await self.daemon.control("GET", "/status", token=None)).json()

    async def reconnect(self) -> None:
        """Bring the daemon back (after a test killed it) and wait until the gateway sees it."""
        if self.daemon.process is None or self.daemon.process.returncode is not None:
            await self.daemon.start()
        await self.daemon.wait_connected()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def desk(tmp_path_factory):
    base = Path(tmp_path_factory.mktemp("real_desktop")).resolve()
    gateway = R.Gateway()
    await gateway.start()
    daemon = R.Daemon(base, gateway)

    # A directory of shims the daemon's commands find first on PATH: they record every call and
    # refuse the two destructive shapes outright, so a daemon that wrongly let `rm -rf /` or
    # `curl | sh` through leaves a log line and not a wiped machine.
    shim_dir = base / "shims"
    shim_dir.mkdir()
    shim_log = base / "shim.log"
    real_rm = shutil_which("rm")
    (shim_dir / "rm").write_text(
        "#!/bin/sh\n"
        f'echo "rm $*" >> "{shim_log}"\n'
        'for a in "$@"; do case "$a" in /|/\\*|"$HOME"|~) exit 0;; esac; done\n'
        f'exec "{real_rm}" "$@"\n'
    )
    (shim_dir / "curl").write_text(f'#!/bin/sh\necho "curl $*" >> "{shim_log}"\nexit 0\n')
    for shim in ("rm", "curl"):
        (shim_dir / shim).chmod(0o755)
    daemon.extra_env["PATH"] = f"{shim_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}"

    trusted = R.make_repo(base / "trusted", {"README.md": "# trusted\n", "src/app.py": "print('hi')\n"})
    untrusted = R.make_repo(base / "untrusted", {"hello.txt": "hello from the untrusted root\n"})
    outside = base / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("TOP SECRET\n")
    await daemon.grant(trusted, trusted=True)
    await daemon.grant(untrusted, trusted=False)

    await daemon.pair(gateway.operator_token())
    await daemon.start()
    await daemon.wait_connected()

    desk = Desk(gateway, daemon, base)
    desk.trusted, desk.untrusted, desk.outside, desk.shim_log = trusted, untrusted, outside, shim_log
    try:
        yield desk
    finally:
        await daemon.stop()
        await gateway.stop()


def shutil_which(name: str) -> str:
    import shutil

    found = shutil.which(name)
    assert found, f"{name} not found"
    return found


@pytest_asyncio.fixture(autouse=True, loop_scope="module")
async def _daemon_is_up(desk):
    """Each test starts with a connected daemon, whatever the previous one did to it."""
    await desk.reconnect()
    yield


def _stdout(content: str) -> str:
    """The stdout section of ``run_command``'s result text."""
    head, _, rest = content.partition("--- stdout ---\n")
    out, _, _err = rest.partition("\n--- stderr ---\n")
    return out


def _stderr(content: str) -> str:
    return content.partition("--- stderr ---\n")[2]


def _exit_code(content: str) -> int:
    return int(re.match(r"exit_code=(-?\d+)", content).group(1))  # type: ignore[union-attr]


# -- 1. pair and connect -------------------------------------------------------------------------


class TestPairAndConnect:
    async def test_the_daemon_paired_through_the_real_flow_and_is_online(self, desk) -> None:
        status = await desk.status()
        assert status["authenticated"] is True
        assert status["backend_connected"] is True
        assert status["credentials_rejected"] is False
        assert await desk.gw.online(), "the gateway's registry has no session for the operator"
        # The token on the daemon's disk is the device token the gateway issued at pair/poll.
        config = json.loads((desk.daemon.config_dir / "config.json").read_text())
        import services.jwt_auth as jwt_auth

        claims = jwt_auth._decode_access_token(config["auth_token"])
        assert claims["scope"] == "host_agent"
        assert claims["sub"] == R.OPERATOR_UID or claims.get("uid") == R.OPERATOR_UID
        # ... and the gateway lists it as a paired desktop of that account.
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{desk.gw.url}/api/v3/auth/desktop/devices",
                headers={"Authorization": f"Bearer {desk.gw.operator_token()}"},
            )
        assert response.status_code == 200
        assert any(d.get("device_id") == claims.get("device_id") for d in response.json()["devices"])

    async def test_the_device_token_is_good_for_the_socket_only(self, desk) -> None:
        import httpx

        config = json.loads((desk.daemon.config_dir / "config.json").read_text())
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{desk.gw.url}/api/v3/auth/desktop/devices",
                headers={"Authorization": f"Bearer {config['auth_token']}"},
            )
        assert response.status_code in (401, 403), response.text

    async def test_a_forged_token_is_refused_at_the_socket(self, desk) -> None:
        """The route's first frame is the credential; a bad one is closed with 4401, not served."""
        import websockets

        async with websockets.connect(desk.gw.url.replace("http", "ws") + "/api/v3/host/agent") as ws:
            await ws.send(json.dumps({"type": "auth", "token": "not-a-token"}))
            with pytest.raises(websockets.ConnectionClosed) as closed:
                await asyncio.wait_for(ws.recv(), timeout=10)
        assert closed.value.rcvd is not None and closed.value.rcvd.code == 4401

    async def test_a_daemon_that_goes_away_and_comes_back_reconnects_without_pairing_again(
        self, desk
    ) -> None:
        await desk.daemon.stop()
        await R.wait_until(desk.offline, timeout=20, what="the session to drop")
        result, _ = await desk.run("echo unreachable", timeout_s=30)
        assert not result.ok and "desktop" in result.content.lower()

        await desk.daemon.start()  # the stored device token, no `auth pair`
        await desk.daemon.wait_connected()
        result, _ = await desk.run("echo back")
        assert result.ok and "back" in _stdout(result.content)

    async def test_the_daemon_reconnects_by_itself_after_the_gateway_bounces(self, desk) -> None:
        port_url = desk.gw.url
        desk.gw._server.should_exit = True  # uvicorn drains: closes the socket, runs lifespan shutdown
        await asyncio.wait_for(desk.gw._task, timeout=30)
        # The daemon is still running and the gateway's registry lost the session with the socket.
        assert desk.daemon.process.returncode is None
        # A second server on the same port, same app: the daemon's own backoff loop must find it.
        import uvicorn

        config = uvicorn.Config(desk.gw.app, host="127.0.0.1", port=desk.gw.port, log_level="warning",
                                lifespan="off", access_log=False)
        server = uvicorn.Server(config)
        server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        task = asyncio.create_task(server.serve())
        desk.gw._server, desk.gw._task = server, task
        assert desk.gw.url == port_url
        await desk.daemon.wait_connected(timeout=60)
        result, _ = await desk.run("echo after-bounce")
        assert result.ok and "after-bounce" in _stdout(result.content)

    async def test_a_dropped_socket_fails_the_calls_in_flight_at_once(self, desk) -> None:
        marker = f"sleep 4{uuid.uuid4().int % 10**6}.5"
        started = time.monotonic()
        inflight = asyncio.create_task(desk.run(marker, timeout_s=120))
        await R.wait_until(lambda: R.cmdline_pids(marker), timeout=20, what="the command to start")
        await desk.daemon.stop(sig=signal.SIGKILL)
        result, _ = await asyncio.wait_for(inflight, timeout=30)
        elapsed = time.monotonic() - started
        assert not result.ok
        assert "disconnect" in result.content.lower()
        assert elapsed < 25, f"the call hung for {elapsed:.0f}s after the daemon died"
        # What the daemon's death leaves behind is a separate question (see the orphan test).
        for pid in R.cmdline_pids(marker):
            os.kill(pid, signal.SIGKILL)


# -- 2. the Engineer's tools through the relay ---------------------------------------------------


class TestFileTools:
    async def test_write_file_lands_the_exact_bytes_including_non_ascii(self, desk) -> None:
        text = "عربى — مرحبا\n日本語のテキスト\nemoji \U0001F680 ☃ é ü\nplain\n"
        result, _ = await desk.tool("write_file", {"path": "notes/unicode.txt", "content": text})
        assert result.ok, result.content
        assert (desk.trusted / "notes" / "unicode.txt").read_bytes() == text.encode("utf-8")

    async def test_write_file_preserves_crlf_and_overwrites(self, desk) -> None:
        await desk.tool("write_file", {"path": "crlf.txt", "content": "old\n"})
        content = "line1\r\nline2\r\n"
        result, _ = await desk.tool("write_file", {"path": "crlf.txt", "content": content})
        assert result.ok, result.content
        assert (desk.trusted / "crlf.txt").read_bytes() == b"line1\r\nline2\r\n"

    async def test_edit_file_replaces_exactly_one_match(self, desk) -> None:
        target = desk.trusted / "edit_me.txt"
        await desk.tool("write_file", {"path": "edit_me.txt", "content": "alpha\nbeta\ngamma\n"})
        result, _ = await desk.tool("edit_file", {"path": "edit_me.txt", "find": "beta", "replace": "BETA ☃"})
        assert result.ok, result.content
        assert target.read_text(encoding="utf-8") == "alpha\nBETA ☃\ngamma\n"

    async def test_edit_file_refuses_zero_and_many_matches_and_leaves_the_file_alone(self, desk) -> None:
        target = desk.trusted / "edit_many.txt"
        original = "dup\ndup\nonce\n"
        await desk.tool("write_file", {"path": "edit_many.txt", "content": original})
        many, _ = await desk.tool("edit_file", {"path": "edit_many.txt", "find": "dup", "replace": "X"})
        none, _ = await desk.tool("edit_file", {"path": "edit_many.txt", "find": "absent", "replace": "X"})
        assert not many.ok and "found 2" in many.content
        assert not none.ok and "found 0" in none.content
        assert target.read_bytes() == original.encode()

    async def test_edit_file_on_a_crlf_file_is_byte_exact(self, desk) -> None:
        target = desk.trusted / "edit_crlf.txt"
        await desk.tool("write_file", {"path": "edit_crlf.txt", "content": "a\r\nb\r\nc\r\n"})
        ok, _ = await desk.tool("edit_file", {"path": "edit_crlf.txt", "find": "a\r\nb", "replace": "a\r\nB"})
        assert ok.ok, ok.content
        assert target.read_bytes() == b"a\r\nB\r\nc\r\n"
        # LF-only `find` does not match CRLF text: refused, file untouched.
        miss, _ = await desk.tool("edit_file", {"path": "edit_crlf.txt", "find": "B\nc", "replace": "x"})
        assert not miss.ok and "found 0" in miss.content
        assert target.read_bytes() == b"a\r\nB\r\nc\r\n"

    async def test_edit_file_on_a_file_that_is_not_utf8_fails_and_changes_nothing(self, desk) -> None:
        target = desk.trusted / "binary.bin"
        target.write_bytes(b"\xff\xfe\x00binary\x80")
        result, _ = await desk.tool("edit_file", {"path": "binary.bin", "find": "binary", "replace": "x"})
        assert not result.ok
        assert target.read_bytes() == b"\xff\xfe\x00binary\x80"

    async def test_write_files_applies_every_operation(self, desk) -> None:
        await desk.tool("write_file", {"path": "multi/b.txt", "content": "one\ntwo\n"})
        ops = [
            {"path": "multi/a.txt", "content": "fresh ☃\n"},
            {"path": "multi/b.txt", "find": "two", "replace": "TWO"},
        ]
        result, _ = await desk.tool("write_files", {"operations": ops})
        assert result.ok, result.content
        assert (desk.trusted / "multi/a.txt").read_text(encoding="utf-8") == "fresh ☃\n"
        assert (desk.trusted / "multi/b.txt").read_text() == "one\nTWO\n"

    async def test_write_files_is_atomic_when_one_operation_is_invalid(self, desk) -> None:
        await desk.tool("write_file", {"path": "atomic/keep.txt", "content": "keep\n"})
        ops = [
            {"path": "atomic/new.txt", "content": "must not appear\n"},
            {"path": "atomic/keep.txt", "find": "not there", "replace": "x"},
        ]
        result, _ = await desk.tool("write_files", {"operations": ops})
        assert not result.ok
        assert not (desk.trusted / "atomic/new.txt").exists(), "a valid operation ran despite a failing one"
        assert (desk.trusted / "atomic/keep.txt").read_text() == "keep\n"

    async def test_write_files_refuses_a_batch_with_one_escaping_path_and_writes_nothing(self, desk) -> None:
        ops = [
            {"path": "batch_ok.txt", "content": "ok\n"},
            {"path": "../batch_escape.txt", "content": "escape\n"},
        ]
        result, _ = await desk.tool("write_files", {"operations": ops})
        assert not result.ok
        assert not (desk.trusted / "batch_ok.txt").exists()
        assert not (desk.trusted.parent / "batch_escape.txt").exists()


class TestRunCommand:
    async def test_stdout_stderr_and_exit_code_come_back_separately(self, desk) -> None:
        result, _ = await desk.run("echo to-out; echo to-err >&2; exit 3")
        assert result.ok, result.content  # the tool succeeded; the command's exit code is data
        assert _exit_code(result.content) == 3
        assert "to-out" in _stdout(result.content) and "to-err" not in _stdout(result.content)
        assert "to-err" in _stderr(result.content)

    async def test_a_command_really_runs_in_the_granted_folder(self, desk) -> None:
        result, _ = await desk.run("pwd && ls README.md")
        assert result.ok and _exit_code(result.content) == 0
        assert str(desk.trusted) in _stdout(result.content)

    async def test_utf8_output_round_trips(self, desk) -> None:
        result, _ = await desk.run("printf 'héllo ☃ 日本語 مرحبا \\360\\237\\232\\200\\n'")
        assert _exit_code(result.content) == 0
        assert "héllo ☃ 日本語 مرحبا \U0001F680" in _stdout(result.content)

    async def test_non_ascii_in_the_command_line_survives_the_wire(self, desk) -> None:
        result, _ = await desk.run("echo 'ملف-☃' > from_cmd.txt && cat from_cmd.txt")
        assert "ملف-☃" in _stdout(result.content)
        assert (desk.trusted / "from_cmd.txt").read_text(encoding="utf-8") == "ملف-☃\n"

    async def test_invalid_utf8_output_neither_hangs_nor_kills_the_daemon(self, desk) -> None:
        result, _ = await desk.run("printf 'before-\\377\\376-after\\n'")
        assert result.ok, result.content
        assert _exit_code(result.content) == 0
        out = _stdout(result.content)
        assert "before-" in out and "-after" in out
        assert (await desk.status())["backend_connected"] is True

    async def test_a_pdf_can_be_read_with_cat_and_the_daemon_survives_its_bytes(self, desk) -> None:
        pdf = (
            b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
            b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
            b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>\nendobj\n"
            b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
        )
        (desk.trusted / "doc.pdf").write_bytes(pdf)
        result, _ = await desk.run("cat doc.pdf")
        assert result.ok, result.content
        assert _exit_code(result.content) == 0
        assert "%PDF-1.4" in _stdout(result.content) and "%%EOF" in _stdout(result.content)
        again, _ = await desk.run("head -c 8 doc.pdf")
        assert _stdout(again.content).startswith("%PDF-1.4")


class TestGitTools:
    async def test_git_status_diff_commit_and_checkout_act_on_the_real_repository(self, desk) -> None:
        repo = desk.trusted
        corr = uuid.uuid4().hex  # one turn: the tools share one task, as in a real Engineer turn
        await desk.tool("write_file", {"path": "feature.txt", "content": "new feature ☃\n"}, corr=corr)
        await desk.tool("edit_file", {"path": "README.md", "find": "# trusted", "replace": "# trusted!"}, corr=corr)

        status, _ = await desk.tool("git_status", {}, corr=corr)
        assert status.ok, status.content
        assert "feature.txt" in status.content and "README.md" in status.content
        assert "feature.txt" in R.run_git(repo, "status", "--short")  # the same thing, seen by git itself

        diff, _ = await desk.tool("git_diff", {}, corr=corr)
        assert diff.ok and "+# trusted!" in diff.content and "-# trusted\n" in diff.content

        branch, _ = await desk.tool("git_checkout_branch", {"branch": "feature/real-desktop"}, corr=corr)
        assert branch.ok, branch.content
        assert R.run_git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/real-desktop"

        commit, _ = await desk.tool("git_commit", {"message": "real daemon commit ☃"}, corr=corr)
        assert commit.ok, commit.content
        assert R.run_git(repo, "log", "-1", "--format=%s").strip() == "real daemon commit ☃"
        assert R.run_git(repo, "status", "--porcelain").strip() == ""
        assert "feature.txt" in R.run_git(repo, "show", "--stat", "--format=", "HEAD")

        clean, _ = await desk.tool("git_diff", {}, corr=corr)
        assert clean.content.strip() == "(no changes)"
        R.run_git(repo, "checkout", "-q", "main")

    async def test_a_commit_with_nothing_staged_is_reported_as_a_failure(self, desk) -> None:
        result, _ = await desk.tool("git_commit", {"message": "empty"})
        assert not result.ok


class TestEngineerTurnThroughTheKernel:
    async def test_a_scripted_engineer_turn_changes_the_real_folder(self, desk) -> None:
        """The same tools, but dispatched by ``AIKernel`` for an Engineer turn: plan, policy, tool
        execution, desktop routing -- the model is the only thing scripted."""
        from aura_core.domain.identifiers import PlanId, SessionId, StepId, UserId
        from aura_core.domain.planning import Goal, Plan, PlanStep, StepKind
        from aura_core.kernel import TurnRequest
        from aura_core.security.role_capabilities import ENGINEER_AGENT

        def step(sid: str, tool: str, args: dict[str, Any], after: tuple[str, ...] = ()) -> PlanStep:
            return PlanStep(id=StepId(sid), kind=StepKind.TOOL, tool=tool, description=f"{tool}",
                            arguments=args, depends_on=tuple(StepId(a) for a in after))

        class Planner:
            def needs_plan(self, message, *, available_tools=(), force=None, previous_reply=None):
                return True

            def goal_from_message(self, message, *, criteria=()):
                return Goal(description=message)

            async def plan(self, goal, available_tools=()):
                return Plan(id=PlanId.new(), goal=goal, steps=(
                    step("s1", "write_file", {"path": "kernel/out.txt", "content": "from the kernel ☃\n"}),
                    step("s2", "run_command", {"command": "cat kernel/out.txt && wc -c kernel/out.txt"}, ("s1",)),
                ))

        kernel = desk.container.kernel
        original = kernel._deps.planner
        kernel._deps.planner = Planner()
        try:
            result = await asyncio.wait_for(kernel.run(TurnRequest(
                message="write a file and show it", user_id=UserId(R.OPERATOR_UID),
                session_id=SessionId("kernel-turn"), agent="engineer", plan=True,
                capabilities=(ENGINEER_AGENT,), workspace_root=str(desk.trusted), origin="desktop",
            )), timeout=120)
        finally:
            kernel._deps.planner = original
        assert not result.pending_tool_calls, result.pending_tool_calls
        written = desk.trusted / "kernel" / "out.txt"
        assert written.read_text(encoding="utf-8") == "from the kernel ☃\n"
