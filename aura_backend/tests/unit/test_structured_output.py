"""AR-6: structured output on ``POST /api/v3/chat``.

A caller sends ``response_schema``; AURA returns ``data`` only when the model's answer is JSON
matching it, repairs once, and otherwise fails ``422 output_invalid``. The real kernel, router
and guards run; only the model's words are scripted (``EchoProvider``).
"""

from __future__ import annotations

import json

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain import UserId
from aura_core.domain.output_schema import parse_structured
from aura_core.llm.providers.echo import ScriptedTurn

SCHEMA = {
    "type": "object",
    "required": ["summary", "statements"],
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "statements": {"type": "array", "maxItems": 5, "items": {
            "type": "object", "required": ["text", "claim_class", "evidence_ids"],
            "properties": {
                "text": {"type": "string"},
                "claim_class": {"type": "string", "enum": ["inferred", "hypothesized", "unsupported"]},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            },
        }},
    },
}
VALID = {"summary": "Extinction below 0.15 O2.", "statements": [
    {"text": "The flame extinguished at 41 s.", "claim_class": "inferred", "evidence_ids": ["ev_01J8Z"]},
]}
TURN = {"message": "Interpret analysis A-1.", "plan": False, "use_tools": False, "use_memory": False,
        "use_knowledge": False, "response_schema": SCHEMA}


@pytest.fixture
def client(container):
    from fastapi.testclient import TestClient

    async def authenticate(request) -> Principal:
        return Principal(user_id=UserId("flame-operator"), email="op@example.com")

    with TestClient(build_app(container, authenticator=authenticate)) as test_client:
        yield test_client


@pytest.fixture
def echo(container):
    provider = container.router.get("echo")
    provider.reset()
    return provider


def test_a_valid_answer_is_returned_as_data_after_one_generation(client, echo) -> None:
    echo.queue(ScriptedTurn(text=json.dumps(VALID)))
    response = client.post("/api/v3/chat", json=TURN)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == VALID and echo.call_count == 1
    # The test echo declares no JSON mode: the contract is prompt-led and still enforced.
    assert echo.calls[0].response_schema is None


def test_json_mode_is_requested_when_a_provider_supports_it(client, echo, monkeypatch) -> None:
    import dataclasses

    monkeypatch.setattr(echo, "_capabilities", dataclasses.replace(echo.capabilities, supports_json_mode=True))
    echo.queue(ScriptedTurn(text=json.dumps(VALID)))
    assert client.post("/api/v3/chat", json=TURN).status_code == 200
    assert echo.calls[0].response_schema == SCHEMA


def test_the_contract_rides_the_user_turn_never_the_system_prompt(client, echo) -> None:
    echo.queue(ScriptedTurn(text=json.dumps(VALID)))
    client.post("/api/v3/chat", json=TURN)
    messages = echo.calls[0].conversation.messages
    user = [m for m in messages if str(m.role) == "user"][-1]
    assert '"claim_class"' in user.content and "Interpret analysis A-1." in user.content
    assert '"claim_class"' not in echo.last_system_prompt()


def test_one_repair_rescues_a_malformed_answer(client, echo) -> None:
    echo.queue(ScriptedTurn(text="Sure! The flame went out."), ScriptedTurn(text="```json\n" + json.dumps(VALID) + "\n```"))
    response = client.post("/api/v3/chat", json=TURN)
    assert response.status_code == 200, response.text
    assert response.json()["data"] == VALID and echo.call_count == 2
    repair = echo.calls[1].conversation.messages
    assert "$: not JSON" in repair[-1].content and repair[-2].content == "Sure! The flame went out."


def test_two_failures_are_a_422_that_never_echoes_the_answer(client, echo) -> None:
    leaked = {"summary": "SECRET-VALUE-123", "statements": [{"text": "x", "claim_class": "measured", "evidence_ids": []}]}
    echo.queue(ScriptedTurn(text=json.dumps(leaked)), ScriptedTurn(text=json.dumps(leaked)))
    response = client.post("/api/v3/chat", json=TURN)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["error"] == "output_invalid" and body["retryable"] is False and "data" not in body
    assert body["details"]["repaired"] is True
    assert body["details"]["errors"] == ["$.statements[0].claim_class: not one of the declared values"]
    assert "SECRET-VALUE-123" not in response.text and echo.call_count == 2


def test_no_repair_once_the_generation_budget_is_spent(container, echo) -> None:
    import asyncio

    from aura_core.context.engine import ContextRequest
    from aura_core.domain.errors import OutputInvalidError
    from aura_core.domain.identifiers import TurnId
    from aura_core.kernel import TurnRequest
    from aura_core.llm.router import TurnBudget

    kernel = container.kernel
    request = TurnRequest(message="x", user_id=UserId("u"), response_schema=SCHEMA)
    assembled = container.context.assemble(ContextRequest(system_prompt="s", user_message="x"))
    spent = TurnBudget(max_generations=1, generations_used=1)
    with pytest.raises(OutputInvalidError) as failed:
        asyncio.run(kernel._enforce_response_schema(request, assembled, "not json", spent, TurnId.new()))
    assert failed.value.details == {"errors": ["$: not JSON"], "repaired": False}
    assert echo.call_count == 0


@pytest.mark.parametrize("schema", [
    {"type": "array", "items": {"type": "string"}},                          # top level must be an object
    {"type": "object", "properties": {"a": {"$ref": "#/defs/A"}}},           # outside the subset
    {"type": "object", "properties": {"a": {"type": "string", "pattern": "x"}}},
    {"type": "object", "properties": {f"p{i}": {"type": "string", "description": "d" * 100} for i in range(100)}},
])
def test_a_bad_schema_is_refused_before_any_generation(client, echo, schema) -> None:
    response = client.post("/api/v3/chat", json={**TURN, "response_schema": schema})
    assert response.status_code == 422, response.text
    assert echo.call_count == 0


def test_the_stream_refuses_a_schema(client, echo) -> None:
    response = client.post("/api/v3/chat/stream", json=TURN)
    assert response.status_code == 422 and echo.call_count == 0


def test_a_turn_without_a_schema_is_unchanged(client, echo) -> None:
    echo.queue(ScriptedTurn(text="plain prose"))
    response = client.post("/api/v3/chat", json={k: v for k, v in TURN.items() if k != "response_schema"})
    assert response.status_code == 200 and "data" not in response.json()
    assert response.json()["message"] == "plain prose" and echo.calls[0].response_schema is None


@pytest.mark.parametrize("text, ok", [
    (json.dumps(VALID), True),
    ("```json\n" + json.dumps(VALID) + "\n```", True),
    ("```\n" + json.dumps(VALID) + "\n```", True),
    ("Here you go: " + json.dumps(VALID), False),
    (json.dumps(VALID) + " Hope that helps!", False),
    (json.dumps({**VALID, "extra": 1}), False),
])
def test_parse_structured_accepts_one_fence_and_nothing_else(text, ok) -> None:
    value, errors = parse_structured(text, SCHEMA)
    assert (value == VALID and errors == []) if ok else (value is None and errors)
