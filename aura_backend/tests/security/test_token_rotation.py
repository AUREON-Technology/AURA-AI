"""Refresh-token rotation: a refresh token is single-use, including under concurrency.

``services/jwt_auth.py::refresh_token_pair`` rotates on every use. It looked the token up and
deleted it as two separate Redis round trips, so two requests presenting the same token at the
same moment -- across the gateway's uvicorn workers, which share Redis -- could both read it
before either deleted it, and both walk away with a fresh session pair. That is exactly the
stolen-token race rotation exists to close: the thief and the victim refreshing together both
keep a live session, and neither sees the reuse.

Redis is the one piece not run for real here: :class:`_LatentRedis` implements the handful of
commands the refresh store uses, each atomic on its own (as Redis commands are) and each paying a
simulated network round trip, which is what opens the window between a GET and a DEL. The code
under test is the real ``jwt_auth`` module.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

import services.jwt_auth as jwt_auth

USER = {"uid": "user-rotation", "email": "rot@example.com", "role": "User"}


class _LatentRedis:
    """Per-command atomicity plus round-trip latency -- the two properties the race needs."""

    def __init__(self, latency_s: float = 0.02) -> None:
        self._data: dict[str, str] = {}
        self._sets: dict[str, set] = {}
        self._lock = threading.Lock()
        self._latency = latency_s

    # The revocation index (FSA-015): per-user, per-device and per-family sets.
    def sadd(self, key: str, *members: str) -> int:
        self._rtt()
        with self._lock:
            bucket = self._sets.setdefault(key, set())
            before = len(bucket)
            bucket.update(members)
            return len(bucket) - before

    def srem(self, key: str, *members: str) -> int:
        self._rtt()
        with self._lock:
            bucket = self._sets.get(key, set())
            before = len(bucket)
            bucket.difference_update(members)
            return before - len(bucket)

    def smembers(self, key: str) -> set:
        self._rtt()
        with self._lock:
            return set(self._sets.get(key, set()))

    def expire(self, key: str, ttl: int) -> int:
        return 1

    def _rtt(self) -> None:
        time.sleep(self._latency)

    def set(self, key: str, value: str, nx: bool = False):
        self._rtt()
        with self._lock:
            if nx and key in self._data:
                return None
            self._data[key] = value
            return True

    def setex(self, key: str, ttl: int, value: str) -> None:
        self._rtt()
        with self._lock:
            self._data[key] = value

    def get(self, key: str):
        self._rtt()
        with self._lock:
            return self._data.get(key)

    def delete(self, key: str) -> int:
        self._rtt()
        with self._lock:
            return 1 if self._data.pop(key, None) is not None else 0

    def getdel(self, key: str):
        self._rtt()
        with self._lock:
            return self._data.pop(key, None)

    def exists(self, key: str) -> int:
        self._rtt()
        with self._lock:
            return int(key in self._data)


@pytest.fixture(params=["redis", "in_process"])
def store(request, monkeypatch):
    """Both refresh stores the module can run on: shared Redis (production) and the in-process
    fallback (Redis unreachable)."""
    backend = _LatentRedis() if request.param == "redis" else None
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: backend)
    monkeypatch.setattr(jwt_auth, "_local_refresh_store", {})
    return request.param


class TestRotation:
    def test_a_used_refresh_token_cannot_be_reused(self, store) -> None:
        pair = jwt_auth.issue_token_pair(dict(USER))
        rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])
        assert rotated is not None
        assert rotated["refresh_token"] != pair["refresh_token"]
        assert jwt_auth.refresh_token_pair(pair["refresh_token"]) is None

    def test_the_replacement_is_itself_single_use_and_live(self, store) -> None:
        pair = jwt_auth.issue_token_pair(dict(USER))
        rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])
        again = jwt_auth.refresh_token_pair(rotated["refresh_token"])
        assert again is not None
        assert jwt_auth.refresh_token_pair(rotated["refresh_token"]) is None

    def test_reusing_a_rotated_token_revokes_its_replacement_too(self, store) -> None:
        """Audit FSA-015: a token presented again after rotation means two parties hold one
        chain, and one of them is a thief -- so the live successor is revoked with it, and the
        user signs in again (tests/unit/test_session_revocation_index.py covers the mechanism)."""
        pair = jwt_auth.issue_token_pair(dict(USER))
        rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])
        assert jwt_auth.refresh_token_pair(pair["refresh_token"]) is None
        assert jwt_auth.refresh_token_pair(rotated["refresh_token"]) is None

    def test_a_revoked_refresh_token_cannot_be_used(self, store) -> None:
        pair = jwt_auth.issue_token_pair(dict(USER))
        jwt_auth.revoke_refresh_token(pair["refresh_token"])
        assert jwt_auth.refresh_token_pair(pair["refresh_token"]) is None

    def test_an_access_token_is_not_a_refresh_token(self, store) -> None:
        pair = jwt_auth.issue_token_pair(dict(USER))
        assert jwt_auth.refresh_token_pair(pair["access_token"]) is None

    async def test_concurrent_refresh_with_one_token_yields_at_most_one_session(
        self, store
    ) -> None:
        """Eight simultaneous refreshes of the same token, each on its own thread (standing in
        for separate workers): exactly one may succeed."""
        pair = jwt_auth.issue_token_pair(dict(USER))

        results = await asyncio.gather(
            *(asyncio.to_thread(jwt_auth.refresh_token_pair, pair["refresh_token"])
              for _ in range(8))
        )

        winners = [r for r in results if r is not None]
        assert len(winners) == 1, (
            f"{len(winners)} concurrent refreshes of one token each minted a session"
        )

    async def test_concurrent_refresh_through_http_yields_at_most_one_session(
        self, store
    ) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.v2.auth import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        pair = jwt_auth.issue_token_pair(dict(USER))

        def refresh() -> int:
            return client.post(
                "/api/v3/auth/refresh", json={"refresh_token": pair["refresh_token"]}
            ).status_code

        statuses = await asyncio.gather(*(asyncio.to_thread(refresh) for _ in range(6)))
        assert sorted(statuses).count(200) <= 1
        assert all(s in (200, 401) for s in statuses)
        assert client.post(
            "/api/v3/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        ).status_code == 401
