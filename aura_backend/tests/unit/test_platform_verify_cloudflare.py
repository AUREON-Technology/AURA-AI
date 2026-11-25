"""Connecting Cloudflare in the console: the token and the account it belongs to (stage 6, B5).

``HttpPlatformVerifier`` is driven over an in-process transport. A Cloudflare connection now needs
the account id with the token (the Cloudflare tab calls the account's catalog, and a first provider
can be added before any exists), and it proves two things with two cheap reads: the token is a
*user-owned* token the token API accepts (an account-owned ``cfat_`` token gets 401 on
``/user/tokens/verify``, probed live on 2026-11-22), and it can read Workers AI on that account.
"""

from __future__ import annotations

import httpx
import pytest

from aura_core.adapters.platform_verify import HttpPlatformVerifier
from aura_core.config.external_inference import WORKERS_AI_API_ROOT
from aura_core.domain.control import Platform
from aura_core.domain.errors import DependencyUnavailableError, ValidationError

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cfut_token-zq19-never-shown"


def _verifier(handler, *, root: str = WORKERS_AI_API_ROOT) -> HttpPlatformVerifier:
    return HttpPlatformVerifier(cloudflare_api_root=root, transport=httpx.MockTransport(handler))


def _good(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/user/tokens/verify"):
        return httpx.Response(200, json={"success": True, "result": {"id": "tok-1", "status": "active"}})
    return httpx.Response(200, json={"success": True, "result": [], "result_info": {"total_count": 0}})


class TestAGoodToken:
    async def test_it_is_checked_as_a_user_token_and_against_the_account(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return _good(request)

        detail = await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)
        assert "active" in detail and ACCOUNT in detail
        assert [r.url.path for r in seen] == [
            "/client/v4/user/tokens/verify", f"/client/v4/accounts/{ACCOUNT}/ai/models/search",
        ]
        assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)
        assert seen[1].url.params["per_page"] == "1"
        assert TOKEN not in detail


class TestARefusedConnection:
    @pytest.mark.parametrize("target", ["", "   ", "acct-1", "G" * 32, ACCOUNT.upper(), ACCOUNT + "0"])
    async def test_a_missing_or_malformed_account_id_is_refused_before_any_request(self, target: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover - must not run
            raise AssertionError("no request may be made")

        with pytest.raises(ValidationError) as caught:
            await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=target)
        assert "account id" in str(caught.value)

    async def test_an_account_owned_token_is_refused_with_a_message_that_says_why(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.url.path)
            return httpx.Response(401, json={"success": False, "errors": [{"code": 1000, "message": "Invalid API Token"}]})

        with pytest.raises(ValidationError) as caught:
            await _verifier(handler).verify(Platform.CLOUDFLARE, "cfat_account-token", target=ACCOUNT)
        message = str(caught.value)
        assert "user" in message and "cfut_" in message and "account-owned" in message
        assert seen == ["/client/v4/user/tokens/verify"]  # nothing further was tried
        assert "cfat_account-token" not in message

    async def test_a_token_that_is_not_active_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"success": True, "result": {"id": "tok-1", "status": "disabled"}})

        with pytest.raises(ValidationError, match="not active"):
            await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)

    async def test_a_token_that_cannot_read_workers_ai_on_the_account_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/user/tokens/verify"):
                return _good(request)
            return httpx.Response(403, json={"success": False})

        with pytest.raises(ValidationError, match="Workers AI"):
            await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)

    async def test_a_cloudflare_error_is_reported_without_vendor_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/user/tokens/verify"):
                return _good(request)
            return httpx.Response(500, json={"errors": [{"message": "vendor detail that must not leak"}]})

        with pytest.raises(ValidationError) as caught:
            await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)
        assert "500" in str(caught.value) and "vendor detail" not in str(caught.value)

    async def test_an_unreachable_cloudflare_is_a_dependency_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        with pytest.raises(DependencyUnavailableError):
            await _verifier(handler).verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)

    async def test_without_an_api_root_it_is_not_configured(self) -> None:
        with pytest.raises(ValidationError):
            await _verifier(_good, root="").verify(Platform.CLOUDFLARE, TOKEN, target=ACCOUNT)
