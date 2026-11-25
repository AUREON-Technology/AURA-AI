"""``GET /api/v3/turns/{turn_id}/usage`` and the owner reference behind it (migration 0033).

``aura_turn_telemetry`` holds no user id, so a turn's usage can only be handed back to its owner
by a keyed reference stamped on the row (``aura_core.security.owner_ref``). What these pin is the
property that matters: nobody but the owner reads a turn's usage, a missing turn and someone
else's turn are indistinguishable, a deployment without a secret fails closed, and erasing an
account removes its turns. The SQL itself is ``tests/load/test_turn_usage_postgres.py``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from datetime import datetime, timezone

import pytest

import services.jwt_auth as jwt_auth
import services.rbac as rbac
from aura_core.adapters.runtime import InMemoryTelemetrySink
from aura_core.api import build_app
from aura_core.domain.identifiers import CorrelationId, SessionId, TurnId
from aura_core.domain.telemetry import TurnTelemetry
from aura_core.kernel import TurnRequest
from aura_core.security.owner_ref import owner_ref_maker
from aura_service import _build_authenticator

SECRET = "unit-test-signing-secret-0123456789abcdef"


# ------------------------------------------------------------------------------ owner reference


class TestOwnerRef:
    def test_the_same_user_under_the_same_secret_gets_the_same_reference(self) -> None:
        make = owner_ref_maker(lambda: SECRET)
        assert make("alice") == make("alice")

    def test_users_and_secrets_get_different_references(self) -> None:
        assert owner_ref_maker(lambda: SECRET)("alice") != owner_ref_maker(lambda: SECRET)("bob")
        assert owner_ref_maker(lambda: SECRET)("alice") != owner_ref_maker(lambda: SECRET + "x")("alice")

    def test_the_reference_is_not_the_user_id_and_not_a_plain_hash_of_it(self) -> None:
        ref = owner_ref_maker(lambda: SECRET)("alice-sub")
        assert len(ref) == 32 and all(c in "0123456789abcdef" for c in ref)
        assert "alice" not in ref
        assert ref != hashlib.sha256(b"alice-sub").hexdigest()[:32], "unkeyed hashes could be guessed offline"
        assert ref != hmac.new(SECRET.encode(), b"alice-sub", hashlib.sha256).hexdigest()[:32], (
            "the key is derived under a label, so the raw signing secret does not produce it"
        )

    def test_no_secret_or_no_user_means_no_reference(self) -> None:
        assert owner_ref_maker(lambda: "")("alice") is None
        assert owner_ref_maker(lambda: SECRET)("") is None

    def test_the_secret_is_read_when_a_reference_is_made(self) -> None:
        """It is hydrated after settings load, so reading it once at construction would see ''."""
        current = {"value": ""}
        make = owner_ref_maker(lambda: current["value"])
        assert make("alice") is None
        current["value"] = SECRET
        assert make("alice") == owner_ref_maker(lambda: SECRET)("alice")


# --------------------------------------------------------------------------------- in-memory sink


def _turn(owner_ref: str | None, **overrides) -> TurnTelemetry:
    base = dict(
        correlation_id=CorrelationId.new(), turn_id=TurnId.new(), started_at=datetime.now(timezone.utc),
        owner_ref=owner_ref, provider="hosted1", model="m-1", prompt_tokens=120, completion_tokens=30,
        cost_usd=0.0021,
    )
    base.update(overrides)
    return TurnTelemetry(**base)


class TestSinkScoping:
    async def test_the_owner_reads_the_turn_back(self) -> None:
        sink, turn = InMemoryTelemetrySink(), _turn("ref-a")
        await sink.record_turn(turn)
        view = await sink.get_turn_usage(turn.turn_id.value, "ref-a")
        assert view["turn_id"] == turn.turn_id.value
        assert view["provider"] == "hosted1" and view["model"] == "m-1"
        assert view["usage"]["prompt_tokens"] == 120 and view["usage"]["completion_tokens"] == 30
        assert view["usage"]["total_tokens"] == 150 and view["usage"]["cost_usd"] == 0.0021

    async def test_nobody_else_reads_it(self) -> None:
        sink, turn = InMemoryTelemetrySink(), _turn("ref-a")
        await sink.record_turn(turn)
        assert await sink.get_turn_usage(turn.turn_id.value, "ref-b") is None
        assert await sink.get_turn_usage(turn.turn_id.value, "") is None

    async def test_a_turn_recorded_without_an_owner_is_readable_by_no_one(self) -> None:
        sink, turn = InMemoryTelemetrySink(), _turn(None)
        await sink.record_turn(turn)
        assert await sink.get_turn_usage(turn.turn_id.value, "") is None
        assert await sink.get_turn_usage(turn.turn_id.value, "ref-a") is None

    async def test_the_view_carries_no_owner_reference_and_no_content(self) -> None:
        sink, turn = InMemoryTelemetrySink(), _turn("ref-a", session_id=SessionId("s-1"))
        await sink.record_turn(turn)
        view = await sink.get_turn_usage(turn.turn_id.value, "ref-a")
        assert "ref-a" not in repr(view) and "owner" not in repr(view).lower()
        assert "ref-a" not in repr(turn.as_dict()), "the reference never enters the payload, logs or metrics"

    async def test_purge_owner_removes_only_that_owners_turns(self) -> None:
        sink = InMemoryTelemetrySink()
        mine, theirs, unowned = _turn("ref-a"), _turn("ref-b"), _turn(None)
        for t in (mine, theirs, unowned):
            await sink.record_turn(t)
        assert await sink.purge_owner("ref-a") == 1
        assert await sink.get_turn_usage(mine.turn_id.value, "ref-a") is None
        assert await sink.get_turn_usage(theirs.turn_id.value, "ref-b") is not None
        assert await sink.purge_owner("") == 0, "an empty reference must never match the unowned rows"
        assert len(sink) == 2


# ------------------------------------------------------------------------------------- over HTTP


@pytest.fixture(autouse=True)
def _signing_secret(monkeypatch):
    monkeypatch.setenv("AURA_JWT_SECRET", SECRET)
    monkeypatch.delenv("JWT_SECRET", raising=False)


@pytest.fixture
def people(monkeypatch):
    """The bearer token names the caller: ``Bearer alice`` signs in as ``alice``."""

    async def verify(request, credentials):
        return {"uid": credentials.credentials, "email": f"{credentials.credentials}@example.com"}

    monkeypatch.setattr(jwt_auth, "verify_access_token", verify)
    monkeypatch.setattr(rbac, "get_user_role", lambda claims: "User")


@pytest.fixture
def client(container, people):
    from fastapi.testclient import TestClient

    authenticator = _build_authenticator(container.settings, service_keys=container.service_keys)
    with TestClient(build_app(container, authenticator=authenticator)) as test_client:
        yield test_client


def _as(name: str) -> dict:
    return {"authorization": f"Bearer {name}"}


def _chat(client, who: str) -> dict:
    response = client.post("/api/v3/chat", headers=_as(who), json={"message": "hello", "use_tools": False})
    assert response.status_code == 200, response.text
    return response.json()


def _usage(client, who: str, turn_id: str):
    # The record is written after the response is built; give the loop a moment, not a sleep per test.
    for _ in range(40):
        response = client.get(f"/api/v3/turns/{turn_id}/usage", headers=_as(who))
        if response.status_code != 404 or who != "alice":
            return response
        time.sleep(0.05)
    return response


class TestUsageRoute:
    def test_the_owner_reads_what_the_chat_response_already_reported(self, client) -> None:
        reply = _chat(client, "alice")
        response = _usage(client, "alice", reply["turn_id"])
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["turn_id"] == reply["turn_id"]
        assert body["usage"]["prompt_tokens"] == reply["usage"]["prompt_tokens"]
        assert body["usage"]["completion_tokens"] == reply["usage"]["completion_tokens"]
        assert body["usage"]["total_tokens"] == reply["usage"]["prompt_tokens"] + reply["usage"]["completion_tokens"]
        assert body["provider"] == reply["provider"] and body["model"] == reply["model"]

    def test_someone_elses_turn_is_indistinguishable_from_a_missing_one(self, client) -> None:
        reply = _chat(client, "alice")
        assert _usage(client, "alice", reply["turn_id"]).status_code == 200
        theirs = client.get(f"/api/v3/turns/{reply['turn_id']}/usage", headers=_as("bob"))
        missing = client.get(f"/api/v3/turns/{TurnId.new().value}/usage", headers=_as("bob"))
        assert theirs.status_code == missing.status_code == 404
        # Same body once each response's own (caller-supplied) id is masked: nothing says which it is.
        def masked(response, turn_id):
            return str(response.json()).replace(turn_id, "<id>")

        missing_id = missing.request.url.path.split("/")[-2]
        assert masked(theirs, reply["turn_id"]) == masked(missing, missing_id)
        assert theirs.json()["error"] == missing.json()["error"] == "not_found"
        assert set(theirs.json()) == set(missing.json())

    def test_it_needs_a_signed_in_caller(self, client) -> None:
        reply = _chat(client, "alice")
        assert client.get(f"/api/v3/turns/{reply['turn_id']}/usage").status_code in (401, 403)

    def test_a_malformed_turn_id_is_a_422_not_a_lookup(self, client) -> None:
        assert client.get("/api/v3/turns/has%20space%20and%20/usage", headers=_as("alice")).status_code in (404, 422)
        assert client.get("/api/v3/turns/" + "x" * 300 + "/usage", headers=_as("alice")).status_code == 422

    def test_without_a_signing_secret_it_fails_closed(self, client, monkeypatch) -> None:
        reply = _chat(client, "alice")
        assert _usage(client, "alice", reply["turn_id"]).status_code == 200
        monkeypatch.delenv("AURA_JWT_SECRET", raising=False)
        assert client.get(f"/api/v3/turns/{reply['turn_id']}/usage", headers=_as("alice")).status_code == 404

    def test_a_rotated_secret_orphans_old_turns_instead_of_exposing_them(self, client, monkeypatch) -> None:
        reply = _chat(client, "alice")
        assert _usage(client, "alice", reply["turn_id"]).status_code == 200
        monkeypatch.setenv("AURA_JWT_SECRET", SECRET + "-rotated")
        assert client.get(f"/api/v3/turns/{reply['turn_id']}/usage", headers=_as("alice")).status_code == 404
        assert client.get(f"/api/v3/turns/{reply['turn_id']}/usage", headers=_as("bob")).status_code == 404


class TestKernelStampsTheOwner:
    async def test_a_turn_is_recorded_with_its_owners_reference(self, container, user) -> None:
        await container.startup()
        await container.kernel.run(TurnRequest(message="hello", user_id=user))
        recorded = [t for t in container.telemetry_sink._turns if t.owner_ref]
        assert recorded, "the kernel did not stamp an owner reference"
        assert recorded[-1].owner_ref == container.owner_ref(user.value)
        assert user.value not in repr(recorded[-1].as_dict())

    async def test_a_failed_turn_is_owned_too(self, container, user, monkeypatch) -> None:
        """Its usage may be zero, but it is the owner's record, and erasure must reach it."""
        import asyncio

        from aura_core.domain.errors import ValidationError

        await container.startup()

        async def fails(*args, **kwargs):
            raise ValidationError("boom")

        monkeypatch.setattr(container.kernel, "_run_turn", fails)
        before = len(container.telemetry_sink._turns)
        await container.kernel.run(TurnRequest(message="hello", user_id=user))
        await asyncio.sleep(0.05)  # a failed turn's record is written from a scheduled task
        new = list(container.telemetry_sink._turns)[before:]
        assert new and all(t.owner_ref == container.owner_ref(user.value) for t in new)
        assert str(new[-1].outcome) == "error"


# -------------------------------------------------------------------------------------- erasure


async def test_erasing_an_account_removes_its_turns_and_nobody_elses(container, user) -> None:
    from aura_core.domain.identifiers import UserId
    from api.admin_platform import erase_account

    await container.startup()
    other = UserId("someone-else")
    await container.kernel.run(TurnRequest(message="hello", user_id=user))
    await container.kernel.run(TurnRequest(message="hello", user_id=other))
    mine, theirs = container.owner_ref(user.value), container.owner_ref(other.value)
    sink = container.telemetry_sink
    assert any(t.owner_ref == mine for t in sink._turns) and any(t.owner_ref == theirs for t in sink._turns)

    result = await erase_account(container, user.value, unknown_user_ok=True)

    assert result["forgotten"] is True and result["failed"] == []
    assert not any(t.owner_ref == mine for t in sink._turns), "the erased account's turns remain"
    assert any(t.owner_ref == theirs for t in sink._turns), "erasure took another account's turns"
