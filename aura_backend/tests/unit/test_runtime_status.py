"""``GET /api/v3/runtime/status``: whether the caller's computer is connected, and which one.

``connected`` comes from the registry (a real session); the device and the time come from a
best-effort shared record the socket's worker writes (``execution/runtime_presence.py``). The tests
drive the real host-agent WebSocket and read the answer back over HTTP, so the whole path --
authenticator claim, socket, record, route -- is what is asserted.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from aura_core.api import Principal, build_app
from aura_core.domain.errors import AuthenticationError
from aura_core.domain.identifiers import UserId
from aura_core.execution.runtime_presence import PRESENCE_TTL_S, RuntimePresence

NOW = datetime(2026, 11, 23, 9, 0, tzinfo=timezone.utc)


class _RecordingCache:
    """The real in-memory cache, noting the TTL each write asked for."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.ttls: list[int | None] = []

    async def get(self, key):
        return await self._inner.get(key)

    async def set(self, key, value, *, ttl_s=None):
        self.ttls.append(ttl_s)
        await self._inner.set(key, value, ttl_s=ttl_s)

    async def delete(self, key):
        await self._inner.delete(key)


class _BrokenCache:
    async def get(self, key):
        raise ConnectionError("redis down")

    async def set(self, key, value, *, ttl_s=None):
        raise ConnectionError("redis down")

    async def delete(self, key):
        raise ConnectionError("redis down")


class TestPresenceRecord:
    async def test_announce_then_get_returns_the_device_and_the_time(self, container) -> None:
        presence = RuntimePresence(container.cache_backend)
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        assert await presence.get("u1") == {"device_id": "dev-A", "connected_since": NOW.isoformat()}
        assert await presence.get("someone-else") is None

    async def test_a_token_with_no_device_is_recorded_as_none(self, container) -> None:
        presence = RuntimePresence(container.cache_backend)
        await presence.announce("u1", connection_id="c1", device_id=None, connected_at=NOW)
        assert (await presence.get("u1"))["device_id"] is None

    async def test_every_write_expires_so_a_dead_worker_leaves_nothing_behind(self, container) -> None:
        cache = _RecordingCache(container.cache_backend)
        presence = RuntimePresence(cache)
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        await presence.refresh("u1", "c1")
        assert cache.ttls == [PRESENCE_TTL_S, PRESENCE_TTL_S]
        assert PRESENCE_TTL_S > 60, "must outlive the socket's 60 s credential recheck that refreshes it"

    async def test_clear_removes_the_record(self, container) -> None:
        presence = RuntimePresence(container.cache_backend)
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        await presence.clear("u1", "c1")
        assert await presence.get("u1") is None

    async def test_an_older_connection_cannot_clear_or_extend_a_newer_ones_record(self, container) -> None:
        cache = _RecordingCache(container.cache_backend)
        presence = RuntimePresence(cache)
        await presence.announce("u1", connection_id="old", device_id="dev-A", connected_at=NOW)
        await presence.announce("u1", connection_id="new", device_id="dev-B", connected_at=NOW + timedelta(minutes=1))
        writes_before = len(cache.ttls)

        await presence.refresh("u1", "old")
        await presence.clear("u1", "old")

        assert (await presence.get("u1"))["device_id"] == "dev-B"
        assert len(cache.ttls) == writes_before, "the stale connection wrote the newer record"

    async def test_refreshing_a_cleared_record_does_not_bring_it_back(self, container) -> None:
        presence = RuntimePresence(container.cache_backend)
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        await presence.clear("u1", "c1")
        await presence.refresh("u1", "c1")
        assert await presence.get("u1") is None

    async def test_a_broken_cache_is_unknown_never_an_error(self) -> None:
        presence = RuntimePresence(_BrokenCache())
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        await presence.refresh("u1", "c1")
        await presence.clear("u1", "c1")
        assert await presence.get("u1") is None

    async def test_no_cache_at_all_is_unknown(self) -> None:
        presence = RuntimePresence(None)
        await presence.announce("u1", connection_id="c1", device_id="dev-A", connected_at=NOW)
        assert await presence.get("u1") is None


def _app(container, *, engineers: frozenset[str] = frozenset({"alice", "bob"})):
    """Tokens are ``<user>`` or ``<user>:<device>``: the device rides the token, as a paired
    daemon's does (``device_id`` claim)."""

    async def authenticator(request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            raise AuthenticationError("missing")
        user, _, device = header[7:].partition(":")
        capabilities = ("engineer_agent",) if user in engineers else ()
        return Principal(user_id=UserId(user), role="User", capabilities=capabilities, device_id=device or None)

    return build_app(container, authenticator=authenticator)


async def _wait(predicate, tries: int = 100) -> bool:
    for _ in range(tries):
        if await predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _status(client, user: str = "alice"):
    return client.get("/api/v3/runtime/status", headers={"Authorization": f"Bearer {user}"})


class TestStatusRoute:
    @pytest.mark.anyio
    async def test_nothing_connected(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container)) as client:
            response = _status(client)
        assert response.status_code == 200
        assert response.json() == {"connected": False, "device_id": None, "connected_since": None}

    @pytest.mark.anyio
    async def test_it_needs_the_engineer(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        with TestClient(_app(container, engineers=frozenset())) as client:
            assert _status(client).status_code == 403

    @pytest.mark.anyio
    async def test_a_connected_computer_is_reported_with_its_device_and_time_then_gone_after(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        registry, presence = container.host_agent_registry, container.runtime_presence
        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "alice:dev-A"})
                assert await _wait(lambda: _device_is(presence, "alice", "dev-A"))
                assert await registry.resolve(UserId("alice")) is not None

                body = _status(client).json()
                assert body["connected"] is True and body["device_id"] == "dev-A"
                assert datetime.fromisoformat(body["connected_since"]).tzinfo is not None
                # Another person's computer is not this person's.
                assert _status(client, "bob").json() == {"connected": False, "device_id": None, "connected_since": None}

            assert await _wait(lambda: _gone(registry, presence, "alice"))
            assert _status(client).json() == {"connected": False, "device_id": None, "connected_since": None}

    @pytest.mark.anyio
    async def test_a_missing_record_still_says_connected_just_without_the_device(self, container) -> None:
        """The record is a hint: losing it must not make a connected computer look disconnected."""
        from fastapi.testclient import TestClient

        await container.startup()
        registry = container.host_agent_registry
        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "alice:dev-A"})
                assert await _wait(lambda: _device_is(container.runtime_presence, "alice", "dev-A"))
                assert await registry.resolve(UserId("alice")) is not None
                await container.cache_backend.delete("runtime_presence:alice")

                assert _status(client).json() == {"connected": True, "device_id": None, "connected_since": None}

    @pytest.mark.anyio
    async def test_a_newer_connection_wins_and_the_older_one_closing_does_not_erase_it(self, container) -> None:
        from fastapi.testclient import TestClient

        await container.startup()
        presence = container.runtime_presence
        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as first:
                first.send_json({"type": "auth", "token": "alice:dev-A"})
                assert await _wait(lambda: _device_is(presence, "alice", "dev-A"))
                with client.websocket_connect("/api/v3/host/agent") as second:
                    second.send_json({"type": "auth", "token": "alice:dev-B"})
                    assert await _wait(lambda: _device_is(presence, "alice", "dev-B"))
                    first.close()
                    await asyncio.sleep(0.3)  # let the first socket's cleanup run

                    assert _status(client).json()["device_id"] == "dev-B", "the older socket's cleanup erased the newer record"
                    assert _status(client).json()["connected"] is True


class TestTheSocketKeepsTheRecordAlive:
    @pytest.mark.anyio
    async def test_a_connected_socket_keeps_refreshing_its_record(self, container, monkeypatch) -> None:
        """The record expires after ``PRESENCE_TTL_S``; only the socket's own loop renews it, so a
        long-lived connection that stopped doing so would show as unknown after three minutes."""
        from fastapi.testclient import TestClient

        from aura_core.api import host_agent, ws_auth

        monkeypatch.setattr(ws_auth, "DEFAULT_RECHECK_S", 0.05)
        monkeypatch.setattr(host_agent, "_PRESENCE_REFRESH_S", 0.0)
        await container.startup()
        refreshed: list[str] = []
        real_refresh = container.runtime_presence.refresh

        async def spy(user_id, connection_id):
            refreshed.append(connection_id)
            await real_refresh(user_id, connection_id)

        monkeypatch.setattr(container.runtime_presence, "refresh", spy)
        with TestClient(_app(container)) as client:
            with client.websocket_connect("/api/v3/host/agent") as ws:
                ws.send_json({"type": "auth", "token": "alice:dev-A"})
                assert await _wait(lambda: _device_is(container.runtime_presence, "alice", "dev-A"))
                for _ in range(100):
                    if len(refreshed) >= 3:
                        break
                    await asyncio.sleep(0.02)
        assert len(refreshed) >= 3 and len(set(refreshed)) == 1, refreshed


async def _device_is(presence, user: str, device: str) -> bool:
    record = await presence.get(user)
    return bool(record) and record["device_id"] == device


async def _gone(registry, presence, user: str) -> bool:
    return await registry.resolve(UserId(user)) is None and await presence.get(user) is None


class TestTheAuthenticatorCarriesTheDevice:
    """The device id is a signed claim of the access token; the Principal copies it, and a token
    that names no device leaves it ``None``. (The socket's record is only as good as this.)"""

    async def test_a_paired_daemon_token_a_desktop_session_and_a_web_token(self, monkeypatch) -> None:
        import services.jwt_auth as jwt_auth
        from aura_core.config.settings import load_settings
        from aura_service import _build_authenticator

        monkeypatch.setenv("JWT_SECRET", "x" * 48)
        identity = {"uid": "eng-1", "email": "e@example.com", "role": "Developer"}
        daemon = jwt_auth.issue_device_token(
            dict(identity), device_id="dev-daemon", scope="host_agent", device_name="Work laptop",
        )["access_token"]
        desktop = jwt_auth.issue_desktop_session(dict(identity), device_id="dev-app")["access_token"]
        web = jwt_auth.issue_token_pair(dict(identity))["access_token"]
        authenticate = _build_authenticator(load_settings(refresh=True, validate=False))

        class _Request:
            def __init__(self, token: str, scopes: tuple[str, ...] = ()) -> None:
                self.headers = {"authorization": f"Bearer {token}"}
                self.allowed_token_scopes = scopes

        assert (await authenticate(_Request(daemon, ("host_agent",)))).device_id == "dev-daemon"
        assert (await authenticate(_Request(desktop))).device_id == "dev-app"
        assert (await authenticate(_Request(web))).device_id is None
