"""Remote control of the paired desktop, backend half (module readiness WP8; D-10, D-10b, D-10c).

The owner allowed the Engineer to work on the person's computer from the phone and web app, with
two guards: remote control is off until switched on at the computer itself (the daemon enforces
that), and every change a remote turn makes there is approved on the remote device. This side
supplies what both guards need:

* the turn's **origin**, derived only from the access token's signed ``platform: desktop`` claim
  (a request body cannot claim to be the desktop);
* a **policy hold** on every desktop-routed side effect of a remote turn, tainted or not, so it goes
  through the existing confirmation flow the phone already renders;
* the **origin and the approval** on the daemon wire (``create_task.origin``,
  ``run/write.confirmed``), and the daemon's granted folders for the picker
  (``GET /api/v3/host/folders``).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.host_agent import (
    HostAgentRegistry,
    HostAgentSandboxRunner,
    HostAgentSession,
)
from aura_core.api import Principal, build_app
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolSensitivity, ToolSpec
from aura_core.ports.coding import TOOL_CALL_CONFIRMED
from aura_core.ports.runtime import ToolContext
from aura_core.tools.policy import ToolPolicy

USER = UserId("eng-1")
CAPS = ("engineer_agent",)


def _context(origin: str | None = None, *, tainted: bool = False) -> ToolContext:
    return ToolContext(
        user_id=USER, correlation_id="c1", deadline_s=30.0, tainted=tainted,
        metadata={"origin": origin} if origin else {}, capabilities=CAPS,
    )


def _run_command_spec() -> ToolSpec:
    return ToolSpec(
        name="run_command", description="run", parameters={},
        sensitivity=ToolSensitivity.SYSTEM, side_effecting=True, workspace_local=True,
        required_capability="engineer_agent",
    )


class TestOrigin:
    def test_only_the_desktop_claim_makes_a_desktop_turn(self) -> None:
        assert Principal(user_id=USER, client_platform="desktop").origin == "desktop"
        assert Principal(user_id=USER).origin == "remote"
        assert Principal(user_id=USER, client_platform="web").origin == "remote"

    async def test_the_authenticator_reads_the_signed_claim(self, monkeypatch) -> None:
        import services.jwt_auth as jwt_auth
        from aura_core.config.settings import load_settings
        from aura_service import _build_authenticator

        monkeypatch.setenv("JWT_SECRET", "x" * 48)
        identity = {"uid": "eng-1", "email": "e@example.com", "role": "Developer"}
        desktop = jwt_auth.issue_desktop_session(dict(identity), device_id="dev-1")["access_token"]
        web = jwt_auth.issue_token_pair(dict(identity))["access_token"]
        authenticate = _build_authenticator(load_settings(refresh=True, validate=False))

        class _Request:
            def __init__(self, token: str) -> None:
                self.headers = {"authorization": f"Bearer {token}"}

        assert (await authenticate(_Request(desktop))).origin == "desktop"
        assert (await authenticate(_Request(web))).origin == "remote"

    def test_a_request_body_cannot_claim_the_desktop(self, container) -> None:
        seen: list[Any] = []

        async def capture(request):
            seen.append(request)
            raise RuntimeError("stop")

        container.kernel.run = capture  # type: ignore[method-assign]

        async def authenticator(_request):
            return Principal(user_id=USER, role="Developer", capabilities=CAPS)

        with TestClient(build_app(container, authenticator=authenticator),
                        raise_server_exceptions=False) as client:
            client.post("/api/v3/chat", json={"message": "hi", "origin": "desktop",
                                              "client_platform": "desktop"})
        assert seen and seen[0].origin == "remote"


class TestThePolicyHold:
    def test_a_remote_turn_s_desktop_side_effect_is_held(self) -> None:
        decision = ToolPolicy().evaluate(
            _run_command_spec(), context=_context("remote"), capabilities=CAPS,
            host_routed=True,
        )
        assert decision.requires_confirmation
        assert decision.reason == "side_effect_on_user_machine_from_remote_device"

    def test_a_desktop_turn_keeps_the_workspace_local_exemption(self) -> None:
        decision = ToolPolicy().evaluate(
            _run_command_spec(), context=_context("desktop"), capabilities=CAPS,
            host_routed=True,
        )
        assert decision.allowed and not decision.requires_confirmation

    def test_the_server_sandbox_is_not_the_person_s_computer(self) -> None:
        decision = ToolPolicy().evaluate(
            _run_command_spec(), context=_context("remote"), capabilities=CAPS,
            host_routed=False,
        )
        assert decision.allowed and not decision.requires_confirmation

    def test_once_approved_it_runs(self) -> None:
        decision = ToolPolicy().evaluate(
            _run_command_spec(), context=_context("remote"), capabilities=CAPS,
            confirmed=True, host_routed=False,
        )
        assert decision.allowed


class _Recorder:
    """A paired daemon that answers every call and records the frames it was sent."""

    def __init__(self) -> None:
        self.frames: list[dict] = []
        self.registry = HostAgentRegistry()
        self.session = HostAgentSession(USER, self._send)

    async def _send(self, frame: dict) -> None:
        self.frames.append(frame)
        method = frame["method"]
        if method == "create_task":
            result: Any = {"task_id": "t1"}
        elif method == "list_roots":
            result = {"roots": [{"path": "/home/eng/app", "trusted": True}],
                      "remote_control": True}
        elif method == "run":
            result = {"stdout": "", "stderr": "", "exit_code": 0}
        else:
            result = {}
        self.session.resolve({"type": "result", "id": frame["id"], "result": result})

    def params(self, method: str) -> dict:
        return next(f["params"] for f in self.frames if f["method"] == method)


class TestTheDaemonWire:
    async def test_create_task_carries_the_origin(self) -> None:
        daemon = _Recorder()
        await daemon.registry.register(daemon.session)
        await HostAgentSandboxRunner(daemon.registry).create_task(user_id=USER, origin="remote")
        assert daemon.params("create_task")["origin"] == "remote"

    async def test_no_origin_sends_the_old_frame(self) -> None:
        daemon = _Recorder()
        await daemon.registry.register(daemon.session)
        await HostAgentSandboxRunner(daemon.registry).create_task(user_id=USER)
        assert "origin" not in daemon.params("create_task")

    async def test_an_approved_call_says_so_and_an_unapproved_one_does_not(self) -> None:
        daemon = _Recorder()
        await daemon.registry.register(daemon.session)
        runner = HostAgentSandboxRunner(daemon.registry)
        handle = await runner.create_task(user_id=USER, origin="remote")

        await runner.run(handle, ["npm", "test"], timeout_s=5)
        assert "confirmed" not in daemon.frames[-1]["params"]

        token = TOOL_CALL_CONFIRMED.set(True)
        try:
            await runner.run(handle, ["npm", "test"], timeout_s=5)
            await runner.write_file(handle, "a.txt", "x")
        finally:
            TOOL_CALL_CONFIRMED.reset(token)
        assert daemon.frames[-2]["params"]["confirmed"] is True
        assert daemon.frames[-1]["params"]["confirmed"] is True

    async def test_the_registry_marks_exactly_the_confirmed_call(self) -> None:
        from aura_core.domain.identifiers import ToolCallId
        from aura_core.domain.messages import ToolCall
        from aura_core.tools.base import FunctionTool
        from aura_core.tools.registry import ToolRegistry

        seen: list[bool] = []

        async def probe(**_kwargs) -> str:
            seen.append(TOOL_CALL_CONFIRMED.get())
            return "ok"

        tool = FunctionTool(ToolSpec(name="probe", description="p", parameters={}), probe)
        registry = ToolRegistry(policy=ToolPolicy())
        registry.register(tool)
        call = ToolCall(id=ToolCallId("1"), name="probe", arguments={})
        await registry.execute(call, _context("remote"), capabilities=CAPS, confirmed=True)
        await registry.execute(call, _context("remote"), capabilities=CAPS)
        assert seen == [True, False]
        assert TOOL_CALL_CONFIRMED.get() is False, "the flag never leaks past its call"


class TestTheFolderPicker:
    def _client(self, container, *, caps=CAPS) -> TestClient:
        async def authenticator(_request):
            return Principal(user_id=USER, role="Developer", capabilities=caps)

        return TestClient(build_app(container, authenticator=authenticator))

    def test_without_the_engineer_it_is_refused(self, container) -> None:
        with self._client(container, caps=()) as client:
            assert client.get("/api/v3/host/folders").status_code == 403

    def test_no_paired_computer(self, container) -> None:
        with self._client(container) as client:
            assert client.get("/api/v3/host/folders").json() == {
                "connected": False, "remote_control": None, "roots": [],
            }

    def test_the_computer_s_own_folders_and_setting(self, container) -> None:
        daemon = _Recorder()
        container.host_agent_registry = daemon.registry
        with self._client(container) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            body = client.get("/api/v3/host/folders").json()
        # is_git (WP4) is None here: this fake daemon predates the field.
        assert body == {"connected": True, "remote_control": True,
                        "roots": [{"path": "/home/eng/app", "trusted": True, "is_git": None}]}

    def test_an_older_daemon_reads_as_not_connected(self, container) -> None:
        registry = HostAgentRegistry()

        async def send(frame):
            session.resolve({"type": "error", "id": frame["id"], "error": "unknown method"})

        session = HostAgentSession(USER, send)
        container.host_agent_registry = registry
        with self._client(container) as client:
            client.portal.call(registry.register, session)
            body = client.get("/api/v3/host/folders").json()
        assert body["connected"] is False


class _GitRecorder(_Recorder):
    """A daemon whose folders say whether they are git repositories, and that can init one."""

    def __init__(self, *, init_error: str | None = None) -> None:
        super().__init__()
        self._init_error = init_error

    async def _send(self, frame: dict) -> None:
        method = frame["method"]
        if method == "list_roots":
            self.frames.append(frame)
            self.session.resolve({"type": "result", "id": frame["id"], "result": {
                "roots": [{"path": "/home/eng/app", "trusted": True, "is_git": True},
                          {"path": "/home/eng/notes", "trusted": False, "is_git": False}],
                "remote_control": True,
            }})
            return
        if method == "init_git":
            self.frames.append(frame)
            if self._init_error:
                self.session.resolve({"type": "error", "id": frame["id"],
                                      "message": self._init_error, "code": "remote_control_disabled"})
            else:
                self.session.resolve({"type": "result", "id": frame["id"],
                                      "result": {"ok": True, "commit": "abc1234"}})
            return
        await super()._send(frame)


class TestInitializeGit:
    """WP4 / D-13: isolation needs git, so a non-git folder offers "Initialize git here". The
    folder list says which folders are repositories, and the phone can ask the computer to
    initialize one -- an explicit tap by the person, sent to the daemon as confirmed."""

    def _client(self, container, *, caps=CAPS) -> TestClient:
        async def authenticator(_request):
            return Principal(user_id=USER, role="Developer", capabilities=caps)

        return TestClient(build_app(container, authenticator=authenticator))

    def test_folders_say_which_are_git_repositories(self, container) -> None:
        daemon = _GitRecorder()
        container.host_agent_registry = daemon.registry
        with self._client(container) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            roots = client.get("/api/v3/host/folders").json()["roots"]
        assert roots == [
            {"path": "/home/eng/app", "trusted": True, "is_git": True},
            {"path": "/home/eng/notes", "trusted": False, "is_git": False},
        ]

    def test_an_older_daemon_reports_is_git_as_unknown(self, container) -> None:
        daemon = _Recorder()  # list_roots without is_git
        container.host_agent_registry = daemon.registry
        with self._client(container) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            [root] = client.get("/api/v3/host/folders").json()["roots"]
        assert root["is_git"] is None

    def test_initialize_git_asks_the_computer(self, container) -> None:
        daemon = _GitRecorder()
        container.host_agent_registry = daemon.registry
        with self._client(container) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            response = client.post("/api/v3/host/folders/git-init", json={"path": "/home/eng/notes"})
        assert response.status_code == 200 and response.json() == {"ok": True, "commit": "abc1234"}
        assert daemon.params("init_git") == {"path": "/home/eng/notes", "confirmed": True}

    def test_the_computer_s_refusal_is_passed_on(self, container) -> None:
        daemon = _GitRecorder(init_error="remote_control_disabled: Remote control is off")
        container.host_agent_registry = daemon.registry
        with self._client(container) as client:
            client.portal.call(daemon.registry.register, daemon.session)
            response = client.post("/api/v3/host/folders/git-init", json={"path": "/home/eng/notes"})
        assert response.status_code == 409
        assert response.json()["error"] == "desktop_refused"
        assert "Remote control is off" in response.json()["message"]

    def test_no_computer_is_503(self, container) -> None:
        with self._client(container) as client:
            response = client.post("/api/v3/host/folders/git-init", json={"path": "/x"})
        assert response.status_code == 503

    def test_without_the_engineer_it_is_refused(self, container) -> None:
        with self._client(container, caps=()) as client:
            response = client.post("/api/v3/host/folders/git-init", json={"path": "/x"})
        assert response.status_code == 403
