"""`POST /api/v3/sync` bounds, without a database (audit F40, INT-110).

* **F40** -- a push had no cap on its mutation count; one request could hold a transaction (and a
  SAVEPOINT per row) for as long as its body allowed. Over the cap it is refused before any row
  is read, with a 413 that names the limit -- which the client already answers by resending its
  rows one at a time (`SyncClient._pushBatch`).
* **INT-110** -- a new row's ``created_at`` used to be the push transaction's start time, the same
  value for every row of a batch, so a second device could not order a conversation's messages.
  The client's own creation time is kept now when it is plausible. The behaviour against real
  rows is in ``tests/load/test_sync_postgres.py``; the window rule is pinned here.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

import api.v2.sync as sync
from api.v2.sync import (
    CREATED_AT_FUTURE_ALLOWANCE,
    CREATED_AT_PAST_ALLOWANCE,
    MAX_PUSH_MUTATIONS,
    creation_time,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
_CLIENT = Path(__file__).resolve().parents[3] / "aura_client" / "lib" / "core" / "sync" / "sync_client.dart"


class _NoDatabase:
    """Fails the test if the push reaches the database at all."""

    def __getattr__(self, name):
        raise AssertionError(f"the push touched the database ({name}) before checking its size")


def _app(monkeypatch):
    from fastapi import FastAPI

    from infrastructure.db.base import get_db
    from services.jwt_auth import verify_access_token

    monkeypatch.setattr(sync, "is_configured", lambda: True)
    app = FastAPI()
    app.include_router(sync.router)

    async def _db():
        yield _NoDatabase()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[verify_access_token] = lambda: {"uid": "u1", "email": "u1@aura.test", "role": "User"}
    return app


def _mutation(i: int) -> dict:
    return {"table": "tasks", "op": "delete", "id": f"00000000-0000-0000-0000-{i:012d}",
            "client_updated_at": "2026-11-18T12:00:00Z"}


class TestPushCap:
    async def test_a_push_over_the_cap_is_refused_before_touching_the_database(self, monkeypatch) -> None:
        transport = httpx.ASGITransport(app=_app(monkeypatch))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            response = await http.post(
                "/api/v3/sync", json={"mutations": [_mutation(i) for i in range(MAX_PUSH_MUTATIONS + 1)]},
            )
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert detail["error"] == "too_many_mutations"
        assert detail["limit"] == MAX_PUSH_MUTATIONS
        assert detail["received"] == MAX_PUSH_MUTATIONS + 1

    def test_the_cap_admits_the_real_clients_batch(self) -> None:
        """The client sends `pushBatchSize` mutations per request; the cap must never refuse that."""
        match = re.search(r"pushBatchSize\s*=\s*(\d+)", _CLIENT.read_text(encoding="utf-8"))
        assert match, "SyncClient.pushBatchSize not found"
        assert MAX_PUSH_MUTATIONS >= int(match.group(1))


class TestCreationTime:
    def test_the_payloads_creation_time_is_kept(self) -> None:
        sent = NOW - timedelta(hours=2)
        assert creation_time(sent.isoformat(), NOW - timedelta(minutes=1), now=NOW) == sent

    def test_a_z_suffixed_string_is_read(self) -> None:
        assert creation_time("2026-11-18T10:00:00Z", None, now=NOW) == datetime(2026, 11, 18, 10, tzinfo=timezone.utc)

    def test_without_one_the_envelope_clock_is_used(self) -> None:
        """What today's client sends: no `created_at`, but a clock taken when the row was queued."""
        clock = NOW - timedelta(minutes=3)
        assert creation_time(None, clock, now=NOW) == clock

    def test_a_naive_value_is_read_as_utc(self) -> None:
        assert creation_time(None, datetime(2026, 11, 18, 9), now=NOW) == datetime(2026, 11, 18, 9, tzinfo=timezone.utc)

    def test_a_value_too_far_ahead_falls_back_to_the_server_clock(self) -> None:
        ahead = NOW + CREATED_AT_FUTURE_ALLOWANCE + timedelta(seconds=1)
        assert creation_time(ahead.isoformat(), ahead, now=NOW) == NOW

    def test_a_value_too_far_back_falls_back_to_the_server_clock(self) -> None:
        behind = NOW - CREATED_AT_PAST_ALLOWANCE - timedelta(seconds=1)
        assert creation_time(behind.isoformat(), behind, now=NOW) == NOW

    def test_an_implausible_payload_value_falls_through_to_the_envelope(self) -> None:
        clock = NOW - timedelta(minutes=1)
        assert creation_time("not a date", clock, now=NOW) == clock
        assert creation_time(12345, clock, now=NOW) == clock
        assert creation_time((NOW + timedelta(days=3)).isoformat(), clock, now=NOW) == clock

    def test_the_window_is_bounded_both_ways(self) -> None:
        assert timedelta(0) < CREATED_AT_FUTURE_ALLOWANCE <= timedelta(days=1)
        assert timedelta(days=1) <= CREATED_AT_PAST_ALLOWANCE


@pytest.mark.parametrize("column", ["created_at"])
def test_created_at_is_still_not_a_free_payload_column(column) -> None:
    """Accepted only on insert, through `creation_time` -- never overwritten by a later upsert."""
    assert column in sync._SERVER_OWNED_COLUMNS
