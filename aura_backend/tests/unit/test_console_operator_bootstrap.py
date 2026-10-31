"""A fresh deployment must say why nobody can sign in to the console.

Found by an operator who could not log into their own deployment. The console requires
`Developer` or above; `get_user_role` returns `User` when no env list names the caller; so the
first sign-in on a new deployment is refused with *"console login requires at least Developer
(resolved User)"*. Every word of that is true and it sends the reader to the wrong place --
they look at authentication, or at their Google account, when the cause is three unset
environment variables.

Two things made it worse than a missing setting usually is:

* the variables are read bare (`SUPERADMIN_EMAILS`), unlike every other setting in a deployment,
  which is `AURA_`-something. Configuring `AURA_SUPERADMIN_EMAILS` by analogy did nothing, said
  nothing, and produced the same 403;
* nothing mentioned it at startup, so there was no moment where the deployment admitted it had no
  operator.

These cover the three halves of the fix: the prefixed spelling works, the refusal names the real
cause when nothing is configured, and the ordinary authorization refusal is unchanged when
something is.
"""

from __future__ import annotations

import pytest

from services.rbac import (
    CONSOLE_MIN_ROLE,
    CONSOLE_ROLE_ENV_VARS,
    console_operators_configured,
    get_user_role,
    is_console_operator,
)

#: A verified Google sign-in, as services.security.verify_identity_token reports it. Email-list
#: roles need both keys (SEC-08); tests/security/test_email_role_grants_require_verified_google.py
#: covers what happens without them.
_GOOGLE = {"email_verified": True, "sign_in_provider": "google.com"}

_OPERATOR = "operator@example.com"
_STRANGER = "someone-else@example.com"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path, request):
    """No inherited role lists, and no roles.json from the working directory.

    `_roles_file_role` and `console_operators_configured` both look for `roles.json` relative to
    the process's cwd, so a file in the repository root would silently satisfy every test here.
    """
    for name in CONSOLE_ROLE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"AURA_{name}", raising=False)
    monkeypatch.chdir(tmp_path)


class TestTheAuraPrefixedSpellingWorks:
    """The trap: every other setting is `AURA_`-something, so the prefixed guess is the natural
    one, and it used to fail in total silence."""

    @pytest.mark.parametrize("variable", CONSOLE_ROLE_ENV_VARS)
    def test_the_bare_name_is_honoured(self, monkeypatch, variable: str) -> None:
        monkeypatch.setenv(variable, _OPERATOR)
        assert console_operators_configured()
        assert is_console_operator(get_user_role({"email": _OPERATOR, "uid": "u", **_GOOGLE}))

    @pytest.mark.parametrize("variable", CONSOLE_ROLE_ENV_VARS)
    def test_the_prefixed_name_is_honoured_too(self, monkeypatch, variable: str) -> None:
        monkeypatch.setenv(f"AURA_{variable}", _OPERATOR)
        assert console_operators_configured(), f"AURA_{variable} was ignored"
        assert is_console_operator(get_user_role({"email": _OPERATOR, "uid": "u", **_GOOGLE})), (
            f"AURA_{variable} did not grant a console role"
        )

    def test_the_bare_name_wins_when_both_are_set(self, monkeypatch) -> None:
        """So no existing deployment changes behaviour by adopting this."""
        monkeypatch.setenv("SUPERADMIN_EMAILS", _OPERATOR)
        monkeypatch.setenv("AURA_SUPERADMIN_EMAILS", _STRANGER)

        assert get_user_role({"email": _OPERATOR, "uid": "u", **_GOOGLE}) == "SuperAdmin"
        assert get_user_role({"email": _STRANGER, "uid": "u", **_GOOGLE}) == "User"

    def test_superadmin_is_still_the_only_list_that_mints_superadmin(self, monkeypatch) -> None:
        """Accepting a second spelling must not widen what a list can grant."""
        monkeypatch.setenv("AURA_ADMIN_EMAILS", _OPERATOR)
        assert get_user_role({"email": _OPERATOR, "uid": "u", **_GOOGLE}) == "Admin"


class TestConsoleOperatorsConfigured:
    def test_a_fresh_deployment_has_none(self) -> None:
        assert console_operators_configured() is False

    def test_an_empty_or_whitespace_list_does_not_count(self, monkeypatch) -> None:
        """`SUPERADMIN_EMAILS=""` and `SUPERADMIN_EMAILS=" , "` are both "unset" in effect, and
        would otherwise report the deployment as configured while granting nobody anything."""
        for value in ("", "   ", " , ", ","):
            monkeypatch.setenv("SUPERADMIN_EMAILS", value)
            assert console_operators_configured() is False, f"{value!r} counted as configured"

    def test_a_roles_file_counts_as_configured(self, tmp_path, monkeypatch) -> None:
        """It can grant a console role, so its presence means someone deliberately set this up.
        Whether it names *this* caller is the ordinary authorization question."""
        (tmp_path / "roles.json").write_text('{"someone@example.com": "Admin"}', encoding="utf-8")
        assert console_operators_configured() is True


class TestTheRefusalNamesTheRealCause:
    """Two different failures wear the same 403. Telling them apart is the whole point."""

    @pytest.fixture()
    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from api.v2.auth import router

        app = FastAPI()
        app.include_router(router)
        return TestClient(app, raise_server_exceptions=False)

    @pytest.fixture()
    def _identity(self, monkeypatch):
        """Stub identity verification: this file is about what happens *after* a caller has
        proved who they are, and minting a real Google ID token is neither possible here nor
        the thing under test."""
        import api.v2.auth as auth_module

        async def _verified(request, authorization=None):
            return {"uid": "u", "email": _OPERATOR, "name": "Operator", "picture": None, **_GOOGLE}

        monkeypatch.setattr(auth_module, "verify_identity_token", _verified)
        monkeypatch.setattr(auth_module, "_persist_user", lambda *a, **k: _noop())

    def test_an_unconfigured_deployment_says_so(self, client, _identity) -> None:
        response = client.post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer stub"}
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert "No console operator is configured" in detail
        assert "SUPERADMIN_EMAILS" in detail, (
            "the refusal must name the variable to set, or it is the old unhelpful 403 with "
            "different wording"
        )
        assert CONSOLE_MIN_ROLE not in detail, (
            "an unconfigured deployment must not blame the caller's role -- that is what sent "
            "the original operator looking in the wrong place"
        )

    def test_a_configured_deployment_still_refuses_a_stranger_on_role(
        self, client, monkeypatch, _identity
    ) -> None:
        """The regression guard. Making the unconfigured case friendlier must not turn a real
        authorization refusal into configuration advice -- that would tell every rejected caller
        how the deployment grants roles."""
        monkeypatch.setenv("SUPERADMIN_EMAILS", _STRANGER)

        response = client.post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer stub"}
        )

        assert response.status_code == 403
        detail = response.json()["detail"]
        assert CONSOLE_MIN_ROLE in detail
        assert "SUPERADMIN_EMAILS" not in detail, (
            "a stranger was told which environment variable grants access"
        )

    def test_a_configured_operator_is_admitted(self, client, monkeypatch, _identity) -> None:
        """The other negative control: the gate must still open. A test suite where every path
        ends in 403 would pass all of the above and describe an unusable console."""
        monkeypatch.setenv("SUPERADMIN_EMAILS", _OPERATOR)

        response = client.post(
            "/api/v3/auth/web/token", headers={"Authorization": "Bearer stub"}
        )

        assert response.status_code == 200, response.text
        assert response.json().get("access_token"), "no access token was issued"


async def _noop() -> None:
    return None
