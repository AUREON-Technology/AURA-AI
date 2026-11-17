"""Session-integrity defects across the auth boundary (integration audit 2026-11-17).

* INT-003 -- ``/desktop/pair/approve`` must refuse an approver the host-agent socket would refuse,
  using that socket's own capability rule, instead of pairing a daemon that is then closed with
  4401 forever.
* INT-010 -- an erased account must say so (``account_deleted``) from the post-erasure 401, from
  ``/refresh`` on a session the erasure revoked, and from a *silent* ``/token`` re-exchange, so a
  second device stops instead of recreating the account under the same subject.
* INT-012 -- a ``/refresh`` whose response was lost can be retried with the same token and the
  same client-chosen ``retry_key`` inside a short window; every other re-presentation of a rotated
  token is still a replay that revokes the whole family.

The stores are the real in-process and Redis-shaped ones (``FakeRedis`` from the revocation-index
tests); only the Google identity check behind ``/token`` is replaced, since that is a network
call to Google.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.v2 import auth as auth_api
from aura_core.config.settings import load_settings
from services import device_pairing, jwt_auth
from services.security import verify_token
from tests.unit.test_session_revocation_index import FakeRedis


@pytest.fixture(params=["local", "redis"])
def store(request, monkeypatch):
    fake = FakeRedis() if request.param == "redis" else None
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: fake)
    monkeypatch.setattr(device_pairing, "get_redis_client", lambda: None)
    for name in (
        "_local_refresh_store", "_local_device_owners", "_local_device_meta",
        "_local_refresh_used", "_local_refresh_families", "_local_erased_accounts",
        "_local_reinstated_accounts", "_local_refresh_revoked",
    ):
        getattr(jwt_auth, name, {}).clear()
    jwt_auth._local_revoked_devices.clear()
    device_pairing._local_pairings.clear()
    device_pairing._local_pairing_codes.clear()
    yield fake
    jwt_auth._local_refresh_store.clear()
    jwt_auth._local_erased_accounts.clear()


@pytest.fixture
def identity():
    """Who the (replaced) Google identity check says is calling ``/token``."""
    return {"uid": "alice-sub", "email": "alice@example.com"}


@pytest.fixture
def client(store, identity):
    app = FastAPI()
    app.include_router(auth_api.router)
    app.dependency_overrides[verify_token] = lambda: dict(identity)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def engineer_roles(monkeypatch):
    def configure(*roles: str) -> None:
        monkeypatch.setenv("AURA_ENGINEER_AGENT_ROLES", ",".join(roles))
        load_settings(refresh=True, validate=False)

    yield configure
    monkeypatch.delenv("AURA_ENGINEER_AGENT_ROLES", raising=False)
    load_settings(refresh=True, validate=False)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _erase(uid: str) -> None:
    """What ``api/admin_platform.erase_account`` does to sessions, in its order."""
    jwt_auth.revoke_all_for_uid(uid)
    jwt_auth.revoke_all_devices_for_uid(uid)
    jwt_auth.mark_account_erased(uid)


def _error(response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("error") if isinstance(detail, dict) else None


# --- INT-003 ------------------------------------------------------------------------------------


class TestPairingNeedsTheCapabilityTheSocketNeeds:
    def _start(self, client) -> dict:
        return client.post("/api/v3/auth/desktop/pair/start").json()

    def test_an_approver_without_engineer_agent_is_refused_with_the_reason(
        self, client, engineer_roles,
    ) -> None:
        engineer_roles()  # the default: nobody has it
        approver = jwt_auth.issue_token_pair({"uid": "u1", "email": "u1@example.com", "role": "User"})
        started = self._start(client)

        response = client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": started["user_code"]},
            headers=_bearer(approver["access_token"]),
        )

        assert response.status_code == 403, response.text
        assert "engineer_agent" in response.text
        assert "AURA_ENGINEER_AGENT_ROLES" in response.text, "the operator needs to know the fix"
        # The code is still pending: a refused approval must not consume it or mint a token.
        poll = client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": started["pairing_id"]})
        assert poll.json()["status"] == "pending"

    def test_an_approver_whose_role_is_opted_in_pairs(self, client, engineer_roles) -> None:
        engineer_roles("Developer")
        approver = jwt_auth.issue_token_pair(
            {"uid": "dev-1", "email": "dev@example.com", "role": "Developer"}
        )
        started = self._start(client)

        response = client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": started["user_code"]},
            headers=_bearer(approver["access_token"]),
        )

        assert response.status_code == 200, response.text
        poll = client.post("/api/v3/auth/desktop/pair/poll", json={"pairing_id": started["pairing_id"]})
        assert poll.json()["status"] == "approved"

    def test_the_rule_is_the_socket_s_own(self, monkeypatch, client, engineer_roles) -> None:
        """Pins reuse, not a copy: change what the socket accepts and approval follows."""
        from aura_core.api import host_agent

        engineer_roles()
        monkeypatch.setattr(host_agent, "may_host", lambda principal: True)
        approver = jwt_auth.issue_token_pair({"uid": "u1", "email": "u1@example.com", "role": "User"})
        response = client.post(
            "/api/v3/auth/desktop/pair/approve",
            json={"user_code": self._start(client)["user_code"]},
            headers=_bearer(approver["access_token"]),
        )
        assert response.status_code == 200, response.text


# --- INT-010 ------------------------------------------------------------------------------------


class TestAnErasedAccountSaysSo:
    def test_the_post_erasure_401_is_distinguishable(self, client) -> None:
        other_phone = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        _erase("alice-sub")

        response = client.get("/api/v3/auth/desktop/devices", headers=_bearer(other_phone["access_token"]))

        assert response.status_code == 401
        assert _error(response) == "account_deleted"
        assert "deleted" in response.json()["detail"]["message"]

    def test_refresh_of_a_session_the_erasure_revoked_says_account_deleted(self, client) -> None:
        other_phone = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        _erase("alice-sub")

        response = client.post("/api/v3/auth/refresh", json={"refresh_token": other_phone["refresh_token"]})

        assert response.status_code == 401
        assert _error(response) == "account_deleted"

    def test_refresh_of_an_ordinary_dead_token_does_not(self, client) -> None:
        response = client.post("/api/v3/auth/refresh", json={"refresh_token": "never-issued"})
        assert response.status_code == 401
        assert _error(response) is None

    def test_a_silent_re_exchange_does_not_recreate_the_account(self, client, monkeypatch) -> None:
        persisted: list = []

        async def record(user_info):
            persisted.append(user_info["uid"])

        monkeypatch.setattr(auth_api, "_persist_user", record)
        _erase("alice-sub")

        response = client.post("/api/v3/auth/token")

        assert response.status_code == 401
        assert _error(response) == "account_deleted"
        assert persisted == [], "no users row may be written back for the erased subject"

    def test_an_explicit_sign_in_starts_a_fresh_account(self, client) -> None:
        _erase("alice-sub")

        response = client.post("/api/v3/auth/token", headers={auth_api.SIGN_IN_HEADER: "interactive"})

        assert response.status_code == 200, response.text
        # From then on this is a live account again: a later silent renewal is not refused.
        assert client.post("/api/v3/auth/token").status_code == 200

    def test_another_account_is_unaffected(self, client, identity) -> None:
        _erase("bob-sub")
        assert client.post("/api/v3/auth/token").status_code == 200


# --- INT-012 ------------------------------------------------------------------------------------


class TestALostRefreshResponseCanBeRetried:
    def _refresh(self, client, token: str, retry_key: str | None = None):
        body = {"refresh_token": token}
        if retry_key is not None:
            body["retry_key"] = retry_key
        return client.post("/api/v3/auth/refresh", json=body)

    def test_a_retry_with_the_same_key_gets_a_working_session(self, client) -> None:
        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        lost = self._refresh(client, first["refresh_token"], "k-1")
        assert lost.status_code == 200  # ...and the client never saw it

        retried = self._refresh(client, first["refresh_token"], "k-1")

        assert retried.status_code == 200, retried.text
        renewed = retried.json()["refresh_token"]
        assert renewed not in (first["refresh_token"], lost.json()["refresh_token"])
        assert self._refresh(client, renewed).status_code == 200, "the session survives"

    def test_the_lost_successor_is_spent_by_the_retry(self, client) -> None:
        """Nobody can hold two live branches of one chain: the response that never arrived carries
        a token that must now be a replay, which kills the family as before."""
        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        lost = self._refresh(client, first["refresh_token"], "k-1").json()
        renewed = self._refresh(client, first["refresh_token"], "k-1").json()

        assert self._refresh(client, lost["refresh_token"]).status_code == 401
        assert self._refresh(client, renewed["refresh_token"]).status_code == 401, "family revoked"

    @pytest.mark.parametrize("second_key", [None, "someone-else-s-key"])
    def test_a_replay_without_the_key_still_revokes_the_family(self, client, second_key) -> None:
        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        successor = self._refresh(client, first["refresh_token"], "k-1").json()

        assert self._refresh(client, first["refresh_token"], second_key).status_code == 401
        assert self._refresh(client, successor["refresh_token"]).status_code == 401, (
            "a thief presenting the stolen token must still end the chain"
        )

    def test_a_replay_after_the_successor_was_used_revokes_the_family(self, client) -> None:
        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        successor = self._refresh(client, first["refresh_token"], "k-1").json()
        third = self._refresh(client, successor["refresh_token"], "k-2").json()

        assert self._refresh(client, first["refresh_token"], "k-1").status_code == 401
        assert self._refresh(client, third["refresh_token"]).status_code == 401

    def test_a_retry_after_the_window_is_a_replay(self, client, monkeypatch) -> None:
        monkeypatch.setattr(jwt_auth, "REFRESH_RETRY_GRACE", timedelta(0))
        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        successor = self._refresh(client, first["refresh_token"], "k-1").json()

        assert self._refresh(client, first["refresh_token"], "k-1").status_code == 401
        assert self._refresh(client, successor["refresh_token"]).status_code == 401

    def test_a_desktop_session_keeps_its_device_binding_through_a_retry(self, client, monkeypatch) -> None:
        monkeypatch.setattr(auth_api, "is_console_operator", lambda role: True)
        first = jwt_auth.issue_desktop_session(
            {"uid": "alice-sub", "email": "alice@example.com"}, device_id="laptop",
        )
        self._refresh(client, first["refresh_token"], "k-1")
        retried = self._refresh(client, first["refresh_token"], "k-1")
        assert retried.status_code == 200, retried.text
        import jwt as pyjwt

        claims = pyjwt.decode(retried.json()["access_token"], options={"verify_signature": False})
        assert claims["device_id"] == "laptop"

    def test_a_retry_cannot_outlive_device_revocation(self, client, monkeypatch) -> None:
        monkeypatch.setattr(auth_api, "is_console_operator", lambda role: True)
        first = jwt_auth.issue_desktop_session(
            {"uid": "alice-sub", "email": "alice@example.com"}, device_id="laptop",
        )
        self._refresh(client, first["refresh_token"], "k-1")
        assert jwt_auth.revoke_device("laptop", requesting_uid="alice-sub")
        assert self._refresh(client, first["refresh_token"], "k-1").status_code == 401

    def test_the_retry_key_never_reaches_a_token(self, client) -> None:
        import jwt as pyjwt

        first = jwt_auth.issue_token_pair({"uid": "alice-sub", "email": "alice@example.com"})
        self._refresh(client, first["refresh_token"], "k-1")
        retried = self._refresh(client, first["refresh_token"], "k-1").json()
        claims = pyjwt.decode(retried["access_token"], options={"verify_signature": False})
        assert not any(key.startswith("_") for key in claims), claims
