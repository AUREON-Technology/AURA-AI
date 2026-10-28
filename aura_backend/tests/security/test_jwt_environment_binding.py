"""Access tokens are bound to the deployment that minted them (F-03).

The prod and dev stacks share a VPS. Separate signing keys are the primary control (see
``tests/unit/test_dev_prod_secret_separation.py``); these tests pin the second one: every token
``services/jwt_auth.py`` mints carries ``iss``/``aud``, and verification refuses a token bound to
another deployment -- including one signed with *this* deployment's key, which is exactly the
shape a dev token had while the two stacks shared ``jwt_secret``.

Migration rule under test: a token with NEITHER claim predates the binding and is honoured until
the legacy cut-off, then refused; a token with either claim is held to both.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import services.jwt_auth as jwt_auth

SECRET = "binding-test-secret-not-real-0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(jwt_auth, "_secret", lambda: SECRET)
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    monkeypatch.setattr(jwt_auth, "_local_refresh_store", {})
    for field in ("jwt_issuer", "jwt_audience", "jwt_legacy_tokens_until"):
        monkeypatch.setattr(jwt_auth.settings, field, "")
    for var in ("AURA_JWT_ISSUER", "AURA_JWT_AUDIENCE", "AURA_JWT_LEGACY_TOKENS_UNTIL", "ENVIRONMENT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AURA_ENVIRONMENT", "production")


def _verify(token: str) -> dict:
    return asyncio.run(jwt_auth.verify_access_token(
        None, HTTPAuthorizationCredentials(scheme="Bearer", credentials=token),
    ))


def _refused(token: str) -> HTTPException:
    with pytest.raises(HTTPException) as excinfo:
        _verify(token)
    assert excinfo.value.status_code == 401
    return excinfo.value


def _raw(**claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {"sub": "intruder", "role": "SuperAdmin", "iat": now, "exp": now + timedelta(minutes=10)}
    payload.update(claims)
    return jwt.encode(payload, SECRET, algorithm=jwt_auth.ALGORITHM)


def _claims(token: str) -> dict:
    return jwt.decode(token, options={"verify_signature": False})


class TestMintedTokensAreBound:
    def test_access_token_carries_this_deployments_issuer_and_audience(self) -> None:
        claims = _claims(jwt_auth.create_access_token({"uid": "u1"}))
        assert claims["iss"] == "aura-production"
        assert claims["aud"] == "aura-api-production"

    def test_dev_and_prod_derive_different_bindings(self, monkeypatch) -> None:
        prod = (jwt_auth._issuer(), jwt_auth._audience())
        monkeypatch.setenv("AURA_ENVIRONMENT", "development")
        dev = (jwt_auth._issuer(), jwt_auth._audience())
        assert prod[0] != dev[0] and prod[1] != dev[1]

    def test_extra_claims_cannot_rebind_a_token(self) -> None:
        token = jwt_auth.create_access_token(
            {"uid": "u1"}, extra_claims={"iss": "aura-development", "aud": "aura-api-development"},
        )
        assert _claims(token)["iss"] == "aura-production"
        assert _verify(token)["uid"] == "u1"

    def test_device_token_is_bound_too(self) -> None:
        token = jwt_auth.issue_device_token({"uid": "u1"}, device_id="dev-1")["access_token"]
        assert _claims(token)["aud"] == "aura-api-production"
        assert _verify(token)["device_id"] == "dev-1"


class TestCrossEnvironmentRejection:
    def test_dev_token_is_refused_by_prod_even_with_the_same_key(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_ENVIRONMENT", "development")
        dev_token = jwt_auth.issue_token_pair({"uid": "u1", "role": "User"})["access_token"]
        assert _verify(dev_token)["uid"] == "u1"  # valid where it was minted

        monkeypatch.setenv("AURA_ENVIRONMENT", "production")
        _refused(dev_token)

    def test_prod_token_is_refused_by_dev(self, monkeypatch) -> None:
        prod_token = jwt_auth.create_access_token({"uid": "u1"})
        monkeypatch.setenv("AURA_ENVIRONMENT", "development")
        _refused(prod_token)

    def test_a_token_for_another_audience_is_refused(self, monkeypatch) -> None:
        monkeypatch.setattr(jwt_auth.settings, "jwt_audience", "aura-api-a")
        token = jwt_auth.create_access_token({"uid": "u1"})
        assert _verify(token)["uid"] == "u1"
        monkeypatch.setattr(jwt_auth.settings, "jwt_audience", "aura-api-b")
        _refused(token)

    def test_right_audience_wrong_issuer_is_refused(self) -> None:
        _refused(_raw(iss="aura-development", aud="aura-api-production"))

    def test_right_issuer_wrong_audience_is_refused(self) -> None:
        _refused(_raw(iss="aura-production", aud="aura-api-development"))

    @pytest.mark.parametrize("present", [{"iss": "aura-production"}, {"aud": "aura-api-production"}])
    def test_a_half_bound_token_is_not_treated_as_legacy(self, present) -> None:
        # Nothing this server ever minted has exactly one of the two claims.
        _refused(_raw(**present))


class TestLegacyClaimlessTokens:
    def test_accepted_before_the_cut_off(self, monkeypatch) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", future)
        assert _verify(_raw(sub="legacy-user"))["uid"] == "legacy-user"

    def test_refused_after_the_cut_off(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", "1970-01-01T00:00:00Z")
        _refused(_raw(sub="legacy-user"))

    def test_settings_value_takes_the_cut_off_too(self, monkeypatch) -> None:
        monkeypatch.setattr(jwt_auth.settings, "jwt_legacy_tokens_until", "2000-01-01")
        _refused(_raw(sub="legacy-user"))

    def test_a_malformed_cut_off_fails_closed(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", "next tuesday")
        _refused(_raw(sub="legacy-user"))

    def test_the_built_in_default_is_bounded(self) -> None:
        # Covers one DEVICE_ACCESS_TOKEN_TTL after the binding shipped, not indefinitely.
        assert jwt_auth._legacy_tokens_until() == jwt_auth._DEFAULT_LEGACY_TOKENS_UNTIL
        assert jwt_auth._DEFAULT_LEGACY_TOKENS_UNTIL <= datetime(2027, 1, 1, tzinfo=timezone.utc)

    def test_an_expired_legacy_token_is_still_expired(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", "2999-01-01T00:00:00Z")
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        err = _refused(_raw(iat=past - timedelta(minutes=15), exp=past))
        assert "expired" in err.detail.lower()

    def test_a_legacy_token_with_a_foreign_signature_is_refused(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", "2999-01-01T00:00:00Z")
        now = datetime.now(timezone.utc)
        forged = jwt.encode({"sub": "x", "exp": now + timedelta(minutes=5)}, "a-dev-stack-key-not-this-one-0123456789", algorithm="HS256")
        _refused(forged)


class TestRefresh:
    def test_refresh_mints_a_bound_token_that_verifies(self) -> None:
        pair = jwt_auth.issue_token_pair({"uid": "u-refresh", "email": "r@example.com"})
        rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])
        assert rotated is not None
        claims = _claims(rotated["access_token"])
        assert (claims["iss"], claims["aud"]) == ("aura-production", "aura-api-production")
        assert _verify(rotated["access_token"])["uid"] == "u-refresh"

    def test_a_pre_binding_session_upgrades_on_refresh_after_the_cut_off(self, monkeypatch) -> None:
        # Refresh tokens are opaque and server-side, so a session that only ever held claimless
        # access tokens keeps working: its next refresh mints a bound one.
        pair = jwt_auth.issue_token_pair({"uid": "u-old"})
        monkeypatch.setenv("AURA_JWT_LEGACY_TOKENS_UNTIL", "1970-01-01T00:00:00Z")
        rotated = jwt_auth.refresh_token_pair(pair["refresh_token"])
        assert rotated is not None
        assert _verify(rotated["access_token"])["uid"] == "u-old"
