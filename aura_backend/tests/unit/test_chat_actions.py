"""`POST /api/v3/chat` returns the Personal-OS proposals a turn made, as `actions` (tool audit T-12).

The streamed route and live voice already delivered them as an `actions` frame; the JSON route dropped
them, so a client using it could never apply a `propose_*` result."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from tests.unit.test_autonomy_profile import CAPS
from tests.unit.test_kernel_degradation import _FixedPlanner, _tool_plan

HEADERS = {"Authorization": "Bearer alice"}


def _client(container) -> TestClient:
    async def authenticator(request):
        if request.headers.get("authorization") != "Bearer alice":
            raise AuthenticationError("unknown principal")
        return Principal(user_id=UserId("alice"), role="Developer", capabilities=CAPS)

    return TestClient(build_app(container, authenticator=authenticator))


def test_chat_returns_the_proposed_actions(container) -> None:
    container.kernel._deps.planner = _FixedPlanner(
        _tool_plan("propose_event", {"title": "Dentist", "startTime": "2026-12-01T09:00:00"}))
    with _client(container) as client:
        body = client.post("/api/v3/chat", headers=HEADERS, json={"message": "book the dentist", "plan": True}).json()
    assert [(a["type"], a["params"]["title"]) for a in body["actions"]] == [("add_event", "Dentist")]


def test_chat_always_has_the_field_and_it_is_empty_when_nothing_was_proposed(container) -> None:
    container.kernel._deps.planner = _FixedPlanner(_tool_plan("calculator", {"expression": "2+2"}))
    with _client(container) as client:
        body = client.post("/api/v3/chat", headers=HEADERS, json={"message": "what is 2+2", "plan": True}).json()
    assert body["actions"] == []
