"""SaaS P1-D2: Google Play RTDN push parsing, push-token checks and the Play API client."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone

import httpx
import pytest

from aura_core.adapters.google_play import GooglePlayClient, GooglePushVerifier, parse_push
from aura_core.domain.errors import AuthenticationError, DependencyUnavailableError, ValidationError

PKG = "tech.aureon.app"
SA = "rtdn-push@aura-prod.iam.gserviceaccount.com"
AUD = "https://api.aureon-ai.tech/api/v3/billing/google/notify"


def _push(data: dict, message_id: str = "m-1") -> bytes:
    return json.dumps({"message": {"data": base64.b64encode(json.dumps(data).encode()).decode(),
                                   "messageId": message_id}, "subscription": "projects/p/subscriptions/s"}).encode()


def test_parse_subscription_notification() -> None:
    push = parse_push(_push({"packageName": PKG, "subscriptionNotification": {
        "version": "1.0", "notificationType": 2, "purchaseToken": "tok/1", "subscriptionId": "aura.pro"}}))
    assert (push.message_id, push.purchase_token, push.package_name, push.notification_type) == ("m-1", "tok/1", PKG, 2)
    assert push.voided is False


def test_parse_voided_subscription_and_ignorables() -> None:
    voided = parse_push(_push({"packageName": PKG, "voidedPurchaseNotification": {"purchaseToken": "t", "productType": 1}}))
    assert voided.voided is True and voided.purchase_token == "t"
    assert parse_push(_push({"packageName": PKG, "testNotification": {"version": "1.0"}})) is None
    assert parse_push(_push({"packageName": PKG, "oneTimeProductNotification": {"purchaseToken": "t"}})) is None
    assert parse_push(_push({"packageName": PKG, "voidedPurchaseNotification": {"purchaseToken": "t", "productType": 2}})) is None


@pytest.mark.parametrize("body", [b"", b"[]", b"{}", b'{"message": {}}', b'{"message": {"messageId": "m", "data": "!!"}}',
                                  _push({"subscriptionNotification": {"purchaseToken": "t"}}),
                                  _push({"packageName": PKG, "subscriptionNotification": {}})])
def test_malformed_push_is_a_validation_error(body) -> None:
    with pytest.raises(ValidationError):
        parse_push(body)


async def test_push_token_must_be_from_the_push_account() -> None:
    good = {"email": SA, "email_verified": True, "aud": AUD}
    seen = []

    def verify(token, audience):
        seen.append((token, audience))
        return dict(good)

    await GooglePushVerifier(AUD, SA, verify_token=verify).verify("Bearer abc")
    assert seen == [("abc", AUD)]
    for claims in ({**good, "email": "someone@else.com"}, {**good, "email_verified": False}, {**good, "aud": "other"}):
        with pytest.raises(AuthenticationError):
            await GooglePushVerifier(AUD, SA, verify_token=lambda t, a, c=claims: c).verify("Bearer abc")
    for header in (None, "", "Basic abc", "Bearer "):
        with pytest.raises(AuthenticationError):
            await GooglePushVerifier(AUD, SA, verify_token=verify).verify(header)


async def test_a_token_google_rejects_is_an_authentication_error() -> None:
    def wrong_audience(token, audience):
        raise ValueError("Token has wrong audience")

    with pytest.raises(AuthenticationError):
        await GooglePushVerifier(AUD, SA, verify_token=wrong_audience).verify("Bearer abc")


def _client(handler) -> GooglePlayClient:
    async def token() -> str:
        return "sa-token"

    return GooglePlayClient(PKG, None, http=httpx.AsyncClient(transport=httpx.MockTransport(handler)), token_provider=token)


async def test_play_client_calls_subscriptionsv2_with_a_bearer() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={
            "subscriptionState": "SUBSCRIPTION_STATE_ACTIVE",
            "lineItems": [{"productId": "aura.pro", "expiryTime": "2026-12-17T12:00:00.123Z"}],
            "externalAccountIdentifiers": {"obfuscatedExternalAccountId": "7D1F-TOKEN"},
            "linkedPurchaseToken": "old-token",
            "acknowledgementState": "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        })

    sub = await _client(handler).subscription("a/b?c")
    assert requests[0].headers["Authorization"] == "Bearer sa-token"
    assert requests[0].url.raw_path.decode() == (
        "/androidpublisher/v3/applications/tech.aureon.app/purchases/subscriptionsv2/tokens/a%2Fb%3Fc")
    assert sub.state == "active" and sub.product_id == "aura.pro" and sub.acknowledged
    assert sub.expires_at == datetime(2026, 12, 17, 12, 0, 0, 123000, tzinfo=timezone.utc)
    assert sub.external_account_id == "7d1f-token" and sub.linked_purchase_token == "old-token"


@pytest.mark.parametrize("state,expected", [
    ("SUBSCRIPTION_STATE_ACTIVE", "active"), ("SUBSCRIPTION_STATE_IN_GRACE_PERIOD", "grace"),
    ("SUBSCRIPTION_STATE_ON_HOLD", "on_hold"), ("SUBSCRIPTION_STATE_CANCELED", "canceled"),
    ("SUBSCRIPTION_STATE_EXPIRED", "expired"), ("SUBSCRIPTION_STATE_PAUSED", "paused"),
    ("SOMETHING_NEW", "pending"),
])
async def test_play_states_map(state, expected) -> None:
    sub = await _client(lambda r: httpx.Response(200, json={"subscriptionState": state, "lineItems": []})).subscription("t")
    assert sub.state == expected and sub.expires_at is None and sub.external_account_id is None


async def test_play_errors() -> None:
    with pytest.raises(ValidationError):
        await _client(lambda r: httpx.Response(404, json={})).subscription("t")
    with pytest.raises(DependencyUnavailableError):
        await _client(lambda r: httpx.Response(503, json={})).subscription("t")

    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(DependencyUnavailableError):
        await _client(boom).subscription("t")


def test_play_client_repr_hides_the_key() -> None:
    client = GooglePlayClient(PKG, {"private_key": "-----BEGIN PRIVATE KEY-----SECRET"})
    assert "SECRET" not in repr(client)
