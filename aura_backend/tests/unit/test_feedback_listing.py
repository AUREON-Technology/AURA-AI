"""``GET /api/v3/feedback``: a person reads their own ratings back.

Content-free by design (the stored record holds the prompt, the reply and any typed correction; none of
that is returned), owner-scoped, newest first, filterable by turn. Expectations come from records
written with fixed times, never from the code under test.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.domain.identifiers import UserId
from aura_core.learning.curation import FeedbackRecord

pytest.importorskip("fastapi")

T0 = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)


def _app(container, *, service: bool = False):
    from aura_core.api import Principal, build_app
    from aura_core.domain.errors import AuthenticationError

    class _Key:
        key_id, name, rate_per_minute, principal_id = "svc-1", "flame", 60, "svc-owner"

        @staticmethod
        def allows(method, path):
            return True

    async def authenticate(request):
        if service:
            return Principal.for_service(_Key())
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        return Principal(user_id=UserId(header[7:]), email="", role="User")

    return build_app(container, authenticator=authenticate)


def _client(container, **kw):
    from fastapi.testclient import TestClient

    return TestClient(_app(container, **kw))


def _as(user: str) -> dict:
    return {"Authorization": f"Bearer {user}"}


async def _rate(container, owner: str, rid: str, turn: str, rating: int, minutes: int, **kw) -> None:
    await container.feedback.put(
        FeedbackRecord(id=rid, prompt=kw.get("prompt", "p"), response=kw.get("response", "r"), rating=rating,
                       correction=kw.get("correction"), user_ref=owner, created_at=T0 + timedelta(minutes=minutes),
                       metadata={"turn_id": turn, "comment": kw.get("comment", "")}),
        owner_id=UserId(owner),
    )


def test_a_person_with_no_feedback_gets_an_empty_list(container) -> None:
    with _client(container) as client:
        response = client.get("/api/v3/feedback", headers=_as("alice"))
    assert response.status_code == 200 and response.json() == {"feedback": []}


def test_it_lists_the_callers_ratings_newest_first_and_nobody_elses(container) -> None:
    asyncio.run(_rate(container, "alice", "r1", "turn-1", 5, 0, comment="great"))
    asyncio.run(_rate(container, "alice", "r2", "turn-2", 2, 10))
    asyncio.run(_rate(container, "bob", "r3", "turn-3", 1, 5, comment="bobs secret remark"))
    with _client(container) as client:
        alice = client.get("/api/v3/feedback", headers=_as("alice"))
        bob = client.get("/api/v3/feedback", headers=_as("bob"))

    assert [(f["id"], f["turn_id"], f["rating"]) for f in alice.json()["feedback"]] == [("r2", "turn-2", 2), ("r1", "turn-1", 5)]
    assert alice.json()["feedback"][1]["comment"] == "great" and alice.json()["feedback"][0]["comment"] == ""
    assert alice.json()["feedback"][0]["created_at"] == (T0 + timedelta(minutes=10)).isoformat()
    assert "bob" not in alice.text and "turn-3" not in alice.text
    assert [f["id"] for f in bob.json()["feedback"]] == ["r3"] and "great" not in bob.text


def test_the_prompt_the_reply_and_the_correction_are_never_returned(container) -> None:
    asyncio.run(_rate(container, "alice", "r1", "t1", 3, 0, prompt="SECRET-PROMPT", response="SECRET-REPLY",
                      correction="SECRET-CORRECTION"))
    asyncio.run(_rate(container, "alice", "r2", "t2", 4, 1))
    with _client(container) as client:
        response = client.get("/api/v3/feedback", headers=_as("alice"))
    assert "SECRET" not in response.text
    by_id = {f["id"]: f for f in response.json()["feedback"]}
    assert by_id["r1"]["has_correction"] is True and by_id["r2"]["has_correction"] is False
    assert set(by_id["r1"]) == {"id", "turn_id", "rating", "comment", "has_correction", "created_at"}


def test_it_filters_by_turn_and_honours_the_limit(container) -> None:
    for i in range(5):
        asyncio.run(_rate(container, "alice", f"r{i}", "shared-turn" if i % 2 == 0 else f"turn-{i}", 3, i))
    with _client(container) as client:
        mine = client.get("/api/v3/feedback", headers=_as("alice"), params={"turn_id": "shared-turn"}).json()["feedback"]
        none = client.get("/api/v3/feedback", headers=_as("alice"), params={"turn_id": "no-such-turn"}).json()["feedback"]
        two = client.get("/api/v3/feedback", headers=_as("alice"), params={"limit": 2}).json()["feedback"]
        bobs_turn = client.get("/api/v3/feedback", headers=_as("bob"), params={"turn_id": "shared-turn"}).json()["feedback"]
    assert [f["id"] for f in mine] == ["r4", "r2", "r0"] and none == []
    assert [f["id"] for f in two] == ["r4", "r3"]
    assert bobs_turn == [], "another person's turn id finds nothing of Alice's"


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"turn_id": "x" * 65}])
def test_out_of_range_parameters_are_refused(container, params) -> None:
    with _client(container) as client:
        assert client.get("/api/v3/feedback", headers=_as("alice"), params=params).status_code == 422


def test_a_caller_must_be_signed_in_and_a_service_key_has_no_feedback(container) -> None:
    with _client(container) as client:
        assert client.get("/api/v3/feedback").status_code == 401
    with _client(container, service=True) as client:
        assert client.get("/api/v3/feedback").status_code == 403


def test_a_rating_posted_through_the_api_is_read_back(container) -> None:
    with _client(container) as client:
        posted = client.post("/api/v3/feedback", headers=_as("alice"), json={
            "turn_id": "turn-x", "rating": 4, "comment": "fine", "correction": "use 7", "prompt": "q", "response": "a"})
        listed = client.get("/api/v3/feedback", headers=_as("alice")).json()["feedback"]
    assert posted.status_code == 200
    assert [(f["turn_id"], f["rating"], f["comment"], f["has_correction"]) for f in listed] == [("turn-x", 4, "fine", True)]


def test_an_exhausted_read_budget_is_a_429_and_the_store_is_not_read(container) -> None:
    from aura_core.api import Principal, build_app

    class AlwaysLimited:
        async def check_all(self, **kwargs):  # noqa: ANN003
            class Decision:
                allowed = False
                retry_after_s = 17

            return Decision()

    class Untouched:
        async def list_for_owner(self, *a, **kw):  # pragma: no cover - reaching it is the failure
            raise AssertionError("feedback was read for a caller over their budget")

    async def authenticate(request):
        return Principal(user_id=UserId("alice"), email="", role="User")

    container.feedback = Untouched()
    from fastapi.testclient import TestClient

    with TestClient(build_app(container, authenticator=authenticate, rate_limiter=AlwaysLimited())) as client:
        response = client.get("/api/v3/feedback")
    assert response.status_code == 429 and response.headers["Retry-After"] == "17"
