"""Google Play Real-Time Developer Notifications, verified (SaaS P1-D2).

Play publishes to a Pub/Sub topic; a push subscription POSTs each message to AURA with a
Google-signed OIDC token in ``Authorization: Bearer``. AURA:

1. verifies that token (Google's signature, our audience, the configured push service account,
   ``email_verified``) -- ``GooglePushVerifier``;
2. parses the envelope only for *which* purchase changed (``parse_push``) -- the notification's own
   claims about state are never trusted;
3. asks the Play Developer API for the subscription (``purchases.subscriptionsv2.get``) with the
   Play service account -- ``GooglePlayClient`` -- and acts on that answer only.

``google-auth`` and ``httpx`` are imported inside functions (adapters import vendor libraries
lazily). The service-account JSON is passed in as a dict and never logged or repr'd.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from ..domain.errors import AuthenticationError, DependencyUnavailableError, ValidationError

__all__ = [
    "PLAY_SCOPE",
    "GooglePlayClient",
    "GooglePushVerifier",
    "PlayPush",
    "PlaySubscription",
    "parse_push",
]

PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
_API = "https://androidpublisher.googleapis.com/androidpublisher/v3/applications"

#: ``subscriptionsv2`` states, shortened. Anything unknown is ``pending`` (changes nothing).
_STATES = {
    "SUBSCRIPTION_STATE_ACTIVE": "active",
    "SUBSCRIPTION_STATE_IN_GRACE_PERIOD": "grace",
    "SUBSCRIPTION_STATE_ON_HOLD": "on_hold",
    "SUBSCRIPTION_STATE_PAUSED": "paused",
    "SUBSCRIPTION_STATE_CANCELED": "canceled",
    "SUBSCRIPTION_STATE_EXPIRED": "expired",
    "SUBSCRIPTION_STATE_PENDING": "pending",
    "SUBSCRIPTION_STATE_PENDING_PURCHASE_CANCELED": "pending",
}
#: ``voidedPurchaseNotification.productType`` for a subscription.
_VOIDED_SUBSCRIPTION = 1


@dataclass(frozen=True, slots=True)
class PlayPush:
    message_id: str
    purchase_token: str
    package_name: str
    notification_type: int | None
    #: A refund or chargeback voided the purchase (``voidedPurchaseNotification``).
    voided: bool = False


@dataclass(frozen=True, slots=True)
class PlaySubscription:
    state: str
    product_id: str | None
    expires_at: datetime | None
    external_account_id: str | None
    linked_purchase_token: str | None
    acknowledged: bool


def parse_push(body: bytes) -> PlayPush | None:
    """The Pub/Sub push envelope. ``None`` for messages that change no subscription (Play's test
    notification, one-time products, a voided one-time purchase)."""
    try:
        envelope = json.loads(body)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValidationError("push body is not JSON") from exc
    message = envelope.get("message") if isinstance(envelope, dict) else None
    if not isinstance(message, dict):
        raise ValidationError("push body has no message")
    message_id, data_b64 = message.get("messageId") or message.get("message_id"), message.get("data")
    if not isinstance(message_id, str) or not message_id or not isinstance(data_b64, str):
        raise ValidationError("push message has no messageId or data")
    try:
        data = json.loads(base64.b64decode(data_b64, validate=True))
    except (binascii.Error, ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValidationError("push data is not base64 JSON") from exc
    if not isinstance(data, dict):
        raise ValidationError("push data is not an object")
    package = data.get("packageName")
    if not isinstance(package, str) or not package:
        if "testNotification" in data:
            return None
        raise ValidationError("push data has no packageName")
    sub = data.get("subscriptionNotification")
    voided = data.get("voidedPurchaseNotification")
    if isinstance(sub, dict):
        token, kind = sub.get("purchaseToken"), sub.get("notificationType")
        if not isinstance(token, str) or not token:
            raise ValidationError("subscription notification has no purchaseToken")
        return PlayPush(message_id, token, package, kind if isinstance(kind, int) and not isinstance(kind, bool) else None)
    if isinstance(voided, dict) and voided.get("productType") == _VOIDED_SUBSCRIPTION:
        token = voided.get("purchaseToken")
        if not isinstance(token, str) or not token:
            raise ValidationError("voided purchase notification has no purchaseToken")
        return PlayPush(message_id, token, package, None, voided=True)
    return None  # test notification, one-time product, voided one-time purchase


def _default_verify_token(token: str, audience: str) -> dict:
    from google.auth.transport.requests import Request
    from google.oauth2 import id_token

    return id_token.verify_oauth2_token(token, Request(), audience=audience)


class GooglePushVerifier:
    """Checks the push request's OIDC token. ``verify_token(token, audience) -> claims`` is
    injectable; the default uses Google's certificates (network, run in a thread)."""

    def __init__(self, audience: str, service_account_email: str, *,
                 verify_token: Callable[[str, str], dict] | None = None) -> None:
        if not audience or not service_account_email:
            raise ValueError("audience and service_account_email are required")
        self._audience = audience
        self._email = service_account_email.lower()
        self._verify = verify_token or _default_verify_token

    def parse(self, body: bytes) -> PlayPush | None:
        """``parse_push``, here so the billing engine needs no adapter import."""
        return parse_push(body)

    async def verify(self, authorization: str | None) -> None:
        scheme, _, token = (authorization or "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise AuthenticationError("push request has no bearer token")
        try:
            claims = await asyncio.to_thread(self._verify, token.strip(), self._audience)
        except Exception as exc:  # google-auth raises ValueError / TransportError subclasses
            raise AuthenticationError("push token does not verify") from exc
        if not isinstance(claims, dict) or str(claims.get("email", "")).lower() != self._email \
                or claims.get("email_verified") is not True:
            raise AuthenticationError("push token is not from the configured service account")
        aud = claims.get("aud")
        if aud != self._audience and not (isinstance(aud, list) and self._audience in aud):
            raise AuthenticationError("push token is for another audience")


def _rfc3339(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError("Play returned a malformed time") from exc


class GooglePlayClient:
    """``purchases.subscriptionsv2.get`` with a service-account bearer. ``token_provider`` (async,
    returns a bearer) and ``http`` (an ``httpx.AsyncClient``) are injectable for tests."""

    def __init__(self, package_name: str, credentials_info: dict | None, *, http: Any = None,
                 token_provider: Callable[[], Awaitable[str]] | None = None, timeout_s: float = 15.0) -> None:
        if not package_name:
            raise ValueError("package_name is required")
        if token_provider is None and not credentials_info:
            raise ValueError("credentials_info or token_provider is required")
        self.package_name = package_name
        self._info = credentials_info
        self._http = http
        self._timeout = timeout_s
        self._token_provider = token_provider or self._service_account_token
        self._credentials: Any = None
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:  # never show the service account key
        return f"GooglePlayClient(package_name={self.package_name!r})"

    async def _service_account_token(self) -> str:
        async with self._lock:
            creds = self._credentials
            if creds is None:
                from google.oauth2 import service_account

                creds = self._credentials = service_account.Credentials.from_service_account_info(
                    self._info, scopes=[PLAY_SCOPE])
            expiry = getattr(creds, "expiry", None)
            if not creds.token or expiry is None or expiry.timestamp() - 60 < time.time():
                from google.auth.transport.requests import Request

                await asyncio.to_thread(creds.refresh, Request())
            return creds.token

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def subscription(self, purchase_token: str) -> PlaySubscription:
        url = f"{_API}/{quote(self.package_name, safe='')}/purchases/subscriptionsv2/tokens/{quote(purchase_token, safe='')}"
        try:
            bearer = await self._token_provider()
            response = await self._client().get(url, headers={"Authorization": f"Bearer {bearer}"})
        except Exception as exc:
            raise DependencyUnavailableError("Google Play did not answer", dependency="google_play", cause=exc) from exc
        if response.status_code in (400, 404, 410):
            raise ValidationError("Google Play does not know this purchase")
        if response.status_code >= 300:
            raise DependencyUnavailableError("Google Play refused the request", dependency="google_play",
                                             details={"status": response.status_code})
        try:
            data = response.json()
        except ValueError as exc:
            raise DependencyUnavailableError("Google Play answered with non-JSON", dependency="google_play") from exc
        if not isinstance(data, dict):
            raise DependencyUnavailableError("Google Play answered with non-object", dependency="google_play")
        items = [i for i in data.get("lineItems") or [] if isinstance(i, dict)]
        expiries = [e for e in (_rfc3339(i.get("expiryTime")) for i in items) if e is not None]
        ids = data.get("externalAccountIdentifiers") if isinstance(data.get("externalAccountIdentifiers"), dict) else {}
        external = ids.get("obfuscatedExternalAccountId")
        linked = data.get("linkedPurchaseToken")
        return PlaySubscription(
            state=_STATES.get(str(data.get("subscriptionState")), "pending"),
            product_id=str(items[0]["productId"]) if items and items[0].get("productId") else None,
            expires_at=max(expiries) if expiries else None,
            external_account_id=str(external).lower() if isinstance(external, str) and external else None,
            linked_purchase_token=linked if isinstance(linked, str) and linked else None,
            acknowledged=data.get("acknowledgementState") == "ACKNOWLEDGEMENT_STATE_ACKNOWLEDGED",
        )

    async def acknowledge(self, product_id: str, purchase_token: str) -> None:
        """``purchases.subscriptions.acknowledge``. Play refunds a purchase nobody acknowledged
        within three days; the app acknowledges too, so a failure here is logged, not fatal."""
        url = (f"{_API}/{quote(self.package_name, safe='')}/purchases/subscriptions/"
               f"{quote(product_id, safe='')}/tokens/{quote(purchase_token, safe='')}:acknowledge")
        try:
            bearer = await self._token_provider()
            response = await self._client().post(url, headers={"Authorization": f"Bearer {bearer}"}, json={})
        except Exception as exc:
            raise DependencyUnavailableError("Google Play did not answer", dependency="google_play", cause=exc) from exc
        if response.status_code >= 300:
            raise DependencyUnavailableError("Google Play refused the acknowledgement", dependency="google_play",
                                             details={"status": response.status_code})
