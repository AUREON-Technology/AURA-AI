"""Revoking every device a user owns, the step demotion and erasure take.

A paired desktop holds a 30-day access token with no refresh token, so revoking refresh tokens
alone left it signed in, under its old role, for up to a month.
"""

from __future__ import annotations

import fnmatch

import pytest

import services.jwt_auth as jwt_auth


class _Redis:
    """The handful of commands the device store uses, with Redis's decoded-string replies."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def setex(self, key: str, ttl: int, value: str) -> None:
        self.data[key] = value

    def get(self, key: str):
        return self.data.get(key)

    def exists(self, key: str) -> int:
        return int(key in self.data)

    def scan_iter(self, pattern: str):
        return [k for k in list(self.data) if fnmatch.fnmatch(k, pattern)]


@pytest.fixture(params=["redis", "in_process"])
def store(request, monkeypatch):
    backend = _Redis() if request.param == "redis" else None
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: backend)
    monkeypatch.setattr(jwt_auth, "_local_device_owners", {})
    monkeypatch.setattr(jwt_auth, "_local_revoked_devices", set())
    return backend


def test_every_device_the_user_owns_is_revoked_and_nobody_elses(store) -> None:
    for device in ("d-a", "d-b"):
        jwt_auth.issue_device_token({"uid": "alice", "email": "a@example.com"}, device_id=device)
    jwt_auth.issue_device_token({"uid": "bob", "email": "b@example.com"}, device_id="d-c")

    assert jwt_auth.revoke_all_devices_for_uid("alice") == 2

    assert jwt_auth.is_device_revoked("d-a")
    assert jwt_auth.is_device_revoked("d-b")
    assert not jwt_auth.is_device_revoked("d-c")


def test_a_user_with_no_devices_revokes_nothing(store) -> None:
    assert jwt_auth.revoke_all_devices_for_uid("nobody") == 0
