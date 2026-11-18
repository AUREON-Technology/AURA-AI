"""F58 (stream B2): Fawry's ``messageSignature`` does not cover ``cardToken``, so a token from a
notification is only trusted once Fawry's own card list for the customer profile confirms it."""

from __future__ import annotations

import hashlib

import httpx

from aura_core.adapters.fawry import FawryPaymentProvider

BASE = "https://atfawry.fawrystaging.com/ECommerceWeb/Fawry/"
MERCHANT, SECRET = "MERCH123", "s3cr3t-key"
PROFILE = "w" * 32


def _provider(handler) -> tuple[FawryPaymentProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return FawryPaymentProvider(BASE, MERCHANT, SECRET, http=httpx.AsyncClient(transport=httpx.MockTransport(record))), seen


def _cards(*tokens: str):
    return lambda r: httpx.Response(200, json={
        "cards": [{"token": t, "lastFourDigits": "1234", "brand": "Visa"} for t in tokens],
        "statusCode": 200, "statusDescription": "Operation done successfully"})


async def test_listed_token_is_confirmed_with_a_signed_request() -> None:
    provider, seen = _provider(_cards("tok-other", "tok-9"))
    assert await provider.confirm_card_token(PROFILE, "tok-9") is True
    request = seen[0]
    assert request.method == "GET" and str(request.url).startswith(BASE + "cards/cardToken?")
    assert request.url.params["merchantCode"] == MERCHANT and request.url.params["customerProfileId"] == PROFILE
    assert request.url.params["signature"] == hashlib.sha256(f"{MERCHANT}{PROFILE}{SECRET}".encode()).hexdigest()
    assert SECRET not in str(request.url)


async def test_unlisted_token_is_not_confirmed() -> None:
    provider, _ = _provider(_cards("tok-9"))
    assert await provider.confirm_card_token(PROFILE, "planted") is False


async def test_refusal_or_no_answer_is_not_confirmed_and_never_raises() -> None:
    provider, _ = _provider(lambda r: httpx.Response(200, json={"statusCode": 9901, "statusDescription": "no"}))
    assert await provider.confirm_card_token(PROFILE, "tok-9") is False

    def boom(request):
        raise httpx.ConnectTimeout("slow")

    provider, _ = _provider(boom)
    assert await provider.confirm_card_token(PROFILE, "tok-9") is False
    provider, _ = _provider(lambda r: httpx.Response(200, json={"cards": "nope", "statusCode": 200}))
    assert await provider.confirm_card_token(PROFILE, "tok-9") is False
