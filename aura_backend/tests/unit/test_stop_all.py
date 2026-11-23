"""The remote kill switch: ``POST /api/v3/agents/stop-all`` (ADR-039).

Driven through the real ASGI app with a real, long-running turn: a scripted planner picks a slow
tool, the real executor/registry/policy run it, and the route cancels it mid-flight. What is
observed is the tool coroutine receiving the cancellation, the in-flight HTTP response coming
back as a ``turn_stopped`` answer (not a dropped connection), and -- for a host-routed call -- the
``cancel`` frame reaching the (recording) desktop wire.

One ``TestClient`` is shared by the turn's thread and the stopping call so both run on the same
event loop, as they do in a real worker; ``Task.cancel`` is not thread-safe.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from aura_core.adapters.host_agent import HostAgentSession
from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from aura_core.domain.tools import ToolCategory, ToolSensitivity
from aura_core.tools.base import tool
from tests.unit.test_autonomy_profile import CAPS, ScriptedPlanner

USERS = ("alice", "bob")


class SlowTool:
    """A tool that takes 30 s unless it is cancelled, and says which happened."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.finished = threading.Event()

    def build(self):
        state = self

        @tool("slow_tool", "Takes a long time.", category=ToolCategory.UTILITY,
              sensitivity=ToolSensitivity.PUBLIC, timeout_s=60.0)
        async def slow_tool() -> str:
            state.started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                state.cancelled.set()
                raise
            state.finished.set()
            return "done"

        return slow_tool


def _client(container, *, desktop: bool = False) -> TestClient:
    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or header[7:] not in USERS:
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId(header[7:]), role="Developer", capabilities=CAPS,
                         client_platform="desktop" if desktop else None)

    return TestClient(build_app(container, authenticator=authenticator))


def _auth(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


@pytest.fixture
def slow(container) -> SlowTool:
    state = SlowTool()
    container.tools.register(state.build())
    container.kernel._deps.planner = ScriptedPlanner("slow_tool")
    return state


def _in_thread(fn) -> tuple[threading.Thread, dict]:
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # surfaced by the asserting test
            box["error"] = exc

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


class TestAnInFlightTurnIsStopped:
    def test_stop_all_cancels_the_turn_its_tool_and_answers_the_caller(self, container, slow) -> None:
        with _client(container) as client:
            thread, box = _in_thread(
                lambda: client.post("/api/v3/chat", json={"message": "do the slow thing"},
                                    headers=_auth("alice")))
            assert slow.started.wait(10), "the turn never reached its tool"
            assert container.kernel.active_turns.count("alice") == 1

            began = time.monotonic()
            stop = client.post("/api/v3/agents/stop-all", headers=_auth("alice"))
            thread.join(10)
            elapsed = time.monotonic() - began

            assert not thread.is_alive(), "the stopped turn's request never returned"
            assert "error" not in box, box.get("error")
            assert stop.status_code == 200
            body = stop.json()
            assert body["stopped"] is True and body["turns_stopped"] == 1
            assert body["turns"][0]["kind"] == "run"
            assert body["scope"] == "this_process"

            # The *tool* got the cancellation (it would otherwise have slept 30 s)...
            assert slow.cancelled.is_set() and not slow.finished.is_set()
            assert elapsed < 8
            # ...and the caller was answered, as a stopped turn, not left with a dropped connection.
            response = box["value"]
            assert response.status_code == 409
            assert response.json()["error"] == "turn_stopped"
            assert container.kernel.active_turns.count("alice") == 0

    def test_it_is_idempotent(self, container, slow) -> None:
        with _client(container) as client:
            nothing = client.post("/api/v3/agents/stop-all", headers=_auth("alice"))
            assert nothing.status_code == 200
            assert nothing.json()["turns_stopped"] == 0 and nothing.json()["desktop_sessions_closed"] == 0

            thread, _ = _in_thread(lambda: client.post(
                "/api/v3/chat", json={"message": "go"}, headers=_auth("alice")))
            assert slow.started.wait(10)
            first = client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()
            second = client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()
            thread.join(10)
            assert first["turns_stopped"] == 1 and second["turns_stopped"] == 0

    def test_it_stops_only_the_callers_own_turns(self, container, slow) -> None:
        with _client(container) as client:
            alice, alice_box = _in_thread(lambda: client.post(
                "/api/v3/chat", json={"message": "alice's work"}, headers=_auth("alice")))
            assert slow.started.wait(10)
            slow.started.clear()
            bob, bob_box = _in_thread(lambda: client.post(
                "/api/v3/chat", json={"message": "bob's work"}, headers=_auth("bob")))
            assert slow.started.wait(10)
            assert container.kernel.active_turns.count("alice") == 1
            assert container.kernel.active_turns.count("bob") == 1

            stop = client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()
            alice.join(10)
            assert stop["turns_stopped"] == 1
            assert alice_box["value"].json()["error"] == "turn_stopped"
            assert bob.is_alive(), "Bob's turn must not be touched by Alice's kill switch"
            assert container.kernel.active_turns.count("bob") == 1

            client.post("/api/v3/agents/stop-all", headers=_auth("bob"))
            bob.join(10)
            assert bob_box["value"].json()["error"] == "turn_stopped"

    def test_a_streamed_turn_ends_with_a_turn_stopped_frame(self, container, slow) -> None:
        frames: list[tuple[str, dict]] = []

        def consume() -> None:
            with client.stream("POST", "/api/v3/chat/stream", json={"message": "stream it"},
                               headers=_auth("alice")) as response:
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        frames.append((event, json.loads(line.split(":", 1)[1])))

        with _client(container) as client:
            thread, box = _in_thread(consume)
            assert slow.started.wait(10)
            stop = client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()
            thread.join(10)
            assert not thread.is_alive() and "error" not in box, box.get("error")
            assert stop["turns_stopped"] == 1 and stop["turns"][0]["kind"] == "stream"
            assert slow.cancelled.is_set()
            assert frames[-1][0] == "error" and frames[-1][1]["error"] == "turn_stopped"
            assert container.kernel.active_turns.count("alice") == 0

    def test_a_turn_nobody_stops_still_finishes_normally(self, container) -> None:
        """The wrapper changes nothing for the turns it does not stop."""
        container.kernel._deps.planner = None
        with _client(container) as client:
            response = client.post("/api/v3/chat", json={"message": "hello"}, headers=_auth("alice"))
            assert response.status_code == 200
            assert container.kernel.active_turns.count("alice") == 0


class TestTheDesktop:
    def test_a_host_routed_call_in_flight_is_cancelled_on_the_desktop_and_the_socket_closed(
        self, container
    ) -> None:
        """A real ``run_command`` routed to a paired desktop that never answers; stop-all must
        send the daemon its ``cancel`` frame and close the connection."""
        wire: list[dict] = []
        closed = threading.Event()
        called = threading.Event()

        async def send(frame) -> None:
            wire.append(dict(frame))
            if frame.get("type") == "call" and frame.get("method") not in ("create_task",):
                called.set()
            if frame.get("type") == "call" and frame.get("method") == "create_task":
                # Answer the workspace-open handshake so the command itself gets sent.
                session.resolve({"type": "result", "id": frame["id"],
                                 "result": {"task_id": "t-1"}})

        async def close() -> None:
            closed.set()

        session = HostAgentSession(UserId("alice"), send, close=close)
        container.kernel._deps.planner = ScriptedPlanner("run_command", {"command": "sleep 100"})
        # The desktop app's own token: a phone's turn would be held for approval on the device
        # (WP8) and never reach the wire, which is a different, already-tested rule.
        with _client(container, desktop=True) as client:
            client.portal.call(container.host_agent_registry.register, session)
            thread, box = _in_thread(lambda: client.post(
                "/api/v3/chat", json={"message": "run it"},
                headers=_auth("alice")))
            assert called.wait(10), f"the command never reached the desktop wire: {wire}"
            stop = client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()
            thread.join(10)

        run_call = next(f for f in wire if f.get("type") == "call" and f.get("method") != "create_task")
        assert {"type": "cancel", "id": run_call["id"]} in wire, wire
        assert closed.is_set()
        assert stop["desktop_sessions_closed"] == 1
        assert not thread.is_alive()

    def test_with_no_desktop_nothing_is_closed(self, container) -> None:
        with _client(container) as client:
            assert client.post("/api/v3/agents/stop-all", headers=_auth("alice")).json()[
                "desktop_sessions_closed"] == 0


class TestRevokingDevices:
    def test_revoke_devices_revokes_paired_daemons_and_leaves_the_app_login(self, container) -> None:
        from services import jwt_auth
        from services.device_kill import revoke_paired_devices

        uid = "alice"
        user_info = {"uid": uid, "email": "alice@example.com", "role": "Developer"}
        daemon = jwt_auth.issue_device_token(
            user_info, device_id="dev-daemon-1", scope="host_agent", device_name="Desk")
        jwt_auth.issue_desktop_session(user_info, device_id="dev-app-1", device_name="App")
        assert not jwt_auth.is_device_revoked("dev-daemon-1")
        assert {d["device_id"] for d in jwt_auth.list_devices_for_uid(uid)} >= {"dev-daemon-1", "dev-app-1"}

        container.device_revoker = revoke_paired_devices
        with _client(container) as client:
            plain = client.post("/api/v3/agents/stop-all", headers=_auth("alice"), json={}).json()
            assert plain["devices"] == {"requested": False}
            assert not jwt_auth.is_device_revoked("dev-daemon-1"), "not revoked unless asked"

            body = client.post("/api/v3/agents/stop-all", headers=_auth("alice"),
                               json={"revoke_devices": True}).json()
        revoked = [d["device_id"] for d in body["devices"]["revoked"]]
        assert revoked == ["dev-daemon-1"]
        assert jwt_auth.is_device_revoked("dev-daemon-1")
        assert not jwt_auth.is_device_revoked("dev-app-1")
        assert daemon["access_token"]  # the token existed; it is now refused by verify_access_token

    def test_another_users_device_is_never_revoked(self, container) -> None:
        from services import jwt_auth
        from services.device_kill import revoke_paired_devices

        jwt_auth.issue_device_token(
            {"uid": "bob", "email": "bob@example.com", "role": "Developer"},
            device_id="dev-bob-1", scope="host_agent", device_name="Bob's desk")
        container.device_revoker = revoke_paired_devices
        with _client(container) as client:
            client.post("/api/v3/agents/stop-all", headers=_auth("alice"), json={"revoke_devices": True})
        assert not jwt_auth.is_device_revoked("dev-bob-1")

    def test_without_the_hook_the_route_says_revocation_is_unavailable(self, container) -> None:
        container.device_revoker = None
        with _client(container) as client:
            body = client.post("/api/v3/agents/stop-all", headers=_auth("alice"),
                               json={"revoke_devices": True}).json()
        assert body["devices"] == {"requested": True, "available": False, "revoked": []}


class TestAudit:
    def test_the_stop_is_audited(self, container) -> None:
        recorded: list = []
        original = container.audit.record

        async def spy(event):
            recorded.append(event)
            return await original(event)

        container.audit.record = spy
        with _client(container) as client:
            client.post("/api/v3/agents/stop-all", headers=_auth("alice"))
        stops = [e for e in recorded if e.action == "agents.stop_all"]
        assert len(stops) == 1 and stops[0].resource == "user:alice"
        assert stops[0].detail["turns_stopped"] == 0

    def test_it_needs_a_signed_in_caller(self, container) -> None:
        with _client(container) as client:
            assert client.post("/api/v3/agents/stop-all").status_code in (401, 403)
