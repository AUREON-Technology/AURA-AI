"""A role removed at its source does not survive refresh-token rotation.

``refresh_token_pair`` re-stamps the role from the stored ``user_info``. That dict carried the
``role`` claim this server stamped at the previous issue, and ``get_user_role`` takes the max over
every candidate it is given -- the stale claim included. So once someone was in ADMIN_EMAILS,
removing them changed nothing for as long as they kept refreshing (a 30-day rolling window).
"""

from __future__ import annotations

import jwt
import pytest

import services.jwt_auth as jwt_auth
from services.rbac import stamp_resolved_role

EMAIL = "former-admin@example.com"


@pytest.fixture(autouse=True)
def _in_process_store(monkeypatch):
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    monkeypatch.setattr(jwt_auth, "_local_refresh_store", {})
    for name in ("SUPERADMIN_EMAILS", "ADMIN_EMAILS", "DEVELOPER_EMAILS"):
        monkeypatch.delenv(name, raising=False)


def _role(access_token: str) -> str:
    return jwt.decode(access_token, options={"verify_signature": False})["role"]


def test_removing_an_admin_from_the_env_list_takes_effect_on_the_next_refresh(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", EMAIL)
    pair = jwt_auth.issue_token_pair(stamp_resolved_role({"uid": "u-1", "email": EMAIL}))
    assert _role(pair["access_token"]) == "Admin"

    monkeypatch.delenv("ADMIN_EMAILS")
    rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])

    assert rotated is not None
    assert _role(rotated["access_token"]) == "User"
    # ...and it stays demoted through further rotations rather than drifting back.
    again = jwt_auth.refresh_token_pair(rotated["refresh_token"])
    assert _role(again["access_token"]) == "User"


def test_a_role_still_granted_at_its_source_survives_refresh(monkeypatch):
    monkeypatch.setenv("DEVELOPER_EMAILS", EMAIL)
    pair = jwt_auth.issue_token_pair(stamp_resolved_role({"uid": "u-2", "email": EMAIL}))

    rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])

    assert _role(rotated["access_token"]) == "Developer"
