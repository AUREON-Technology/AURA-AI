"""Fawry (FawryPay) :class:`~aura_core.ports.billing.PaymentProvider` (SaaS P1-D1).

Wire format, from Fawry's Server Notification V2 documentation and the open-source
``fawry-api/fawry`` client (the formulas must be re-confirmed against the merchant's own staging
account before go-live; ``docs/deployment/billing-fawry.md``):

* charge  ``POST payments/charge`` -- ``signature = sha256(merchantCode + merchantRefNum +
  customerProfileId + paymentMethod + amount(2dp) + cardToken? + secureKey)``. ``PAYATFAWRY``
  returns a reference number payable at any Fawry outlet, wallet or myFawry; ``CARD`` with a saved
  ``cardToken`` charges the card.
* status  ``GET payments/status`` -- ``signature = sha256(merchantCode + merchantRefNumber + secureKey)``.
* notification V2 (Fawry -> us) -- ``messageSignature = sha256(fawryRefNumber + merchantRefNum +
  paymentAmount(2dp) + orderAmount(2dp) + orderStatus + paymentMethod + paymentRefrenceNumber? +
  secureKey)`` ("Refrence" is Fawry's spelling).

The secure key never leaves this object: it is not logged, not put in an error's details, and not
sent on the wire (only digests that include it are).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from ..domain.billing import Payment, PaymentStatus
from ..domain.errors import AuthenticationError, DependencyUnavailableError, ValidationError
from ..ports.billing import ProviderCharge, ProviderNotification, ProviderStatus

__all__ = ["FawryPaymentProvider"]

_STATUS = {
    "PAID": PaymentStatus.PAID,
    "UNPAID": PaymentStatus.PENDING,
    "NEW": PaymentStatus.PENDING,
    "EXPIRED": PaymentStatus.EXPIRED,
    "CANCELED": PaymentStatus.FAILED,
    "CANCELLED": PaymentStatus.FAILED,
    "FAILED": PaymentStatus.FAILED,
    "REFUNDED": PaymentStatus.REFUNDED,
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _egp(piastres: int) -> str:
    return f"{piastres // 100}.{piastres % 100:02d}"


def _two_dp(value: Any) -> str:
    try:
        return str(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError("amount is not a number") from exc


def _piastres(value: Any) -> int:
    return int(Decimal(_two_dp(value)) * 100)


class FawryPaymentProvider:
    name = "fawry"

    def __init__(self, base_url: str, merchant_code: str, secure_key: str, *, http: Any = None,
                 timeout_s: float = 20.0) -> None:
        self._base = base_url if base_url.endswith("/") else base_url + "/"
        self._merchant = merchant_code
        self._secret = secure_key
        self._http = http
        self._timeout = timeout_s

    def __repr__(self) -> str:  # never show the key
        return f"FawryPaymentProvider(base={self._base!r}, merchant={self._merchant!r})"

    def _client(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def _charge(self, payment: Payment, *, customer_profile_id: str, method: str,
                      card_token: str | None, email: str | None) -> ProviderCharge:
        amount = _egp(payment.amount_piastres)
        body: dict[str, Any] = {
            "merchantCode": self._merchant,
            "merchantRefNum": payment.id,
            "customerProfileId": customer_profile_id,
            "paymentMethod": method,
            "amount": amount,
            "currencyCode": "EGP",
            "description": f"AURA {payment.plan_id} plan",
            "chargeItems": [{
                "itemId": f"{payment.plan_id}-{payment.seats}",
                "description": f"AURA {payment.plan_id} ({payment.seats} seat{'s' if payment.seats != 1 else ''})",
                "price": amount,
                "quantity": 1,
            }],
            "signature": _sha256(
                f"{self._merchant}{payment.id}{customer_profile_id}{method}{amount}{card_token or ''}{self._secret}"
            ),
        }
        if card_token:
            body["cardToken"] = card_token
        if email:
            body["customerEmail"] = email
        if payment.expires_at is not None:
            body["paymentExpiry"] = int(payment.expires_at.timestamp() * 1000)
        data = await self._request("POST", "payments/charge", json=body)
        return ProviderCharge(
            reference_number=str(data.get("referenceNumber")) if data.get("referenceNumber") is not None else None,
            provider_ref=str(data.get("fawryRefNumber")) if data.get("fawryRefNumber") is not None else None,
        )

    async def create_reference(self, payment: Payment, *, customer_profile_id: str, email: str | None) -> ProviderCharge:
        return await self._charge(payment, customer_profile_id=customer_profile_id, method="PAYATFAWRY",
                                  card_token=None, email=email)

    async def charge_card(self, payment: Payment, *, customer_profile_id: str, card_token: str) -> ProviderCharge:
        return await self._charge(payment, customer_profile_id=customer_profile_id, method="CARD",
                                  card_token=card_token, email=None)

    async def status(self, payment_id: str) -> ProviderStatus:
        data = await self._request("GET", "payments/status", params={
            "merchantCode": self._merchant,
            "merchantRefNumber": payment_id,
            "signature": _sha256(f"{self._merchant}{payment_id}{self._secret}"),
        })
        raw = str(data.get("paymentStatus") or data.get("orderStatus") or "").upper()
        amount = data.get("paymentAmount")
        return ProviderStatus(
            status=_STATUS.get(raw, PaymentStatus.PENDING),
            amount_piastres=_piastres(amount) if amount is not None else None,
            provider_ref=str(data["fawryRefNumber"]) if data.get("fawryRefNumber") is not None else None,
        )

    async def parse_notification(self, body: bytes) -> ProviderNotification:
        try:
            note = json.loads(body)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValidationError("notification is not JSON") from exc
        if not isinstance(note, dict):
            raise ValidationError("notification is not an object")
        required = ("fawryRefNumber", "merchantRefNum", "paymentAmount", "orderAmount", "orderStatus",
                    "paymentMethod", "messageSignature")
        missing = [field for field in required if note.get(field) in (None, "")]
        if missing:
            raise ValidationError("notification is missing fields", details={"missing": missing})
        expected = _sha256(
            f"{note['fawryRefNumber']}{note['merchantRefNum']}{_two_dp(note['paymentAmount'])}"
            f"{_two_dp(note['orderAmount'])}{note['orderStatus']}{note['paymentMethod']}"
            f"{note.get('paymentRefrenceNumber') or ''}{self._secret}"
        )
        if not hmac.compare_digest(expected, str(note["messageSignature"])):
            raise AuthenticationError("notification signature does not match")
        return ProviderNotification(
            payment_id=str(note["merchantRefNum"]),
            status=_STATUS.get(str(note["orderStatus"]).upper(), PaymentStatus.PENDING),
            amount_piastres=_piastres(note["paymentAmount"]),
            provider_ref=str(note["fawryRefNumber"]),
            card_token=note.get("cardToken") or None,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict:
        try:
            response = await self._client().request(method, self._base + path, **kwargs)
            data = response.json()
        except Exception as exc:
            raise DependencyUnavailableError("Fawry did not answer", dependency="fawry", cause=exc) from exc
        status_code = data.get("statusCode") if isinstance(data, dict) else None
        if response.status_code >= 400 or status_code not in (200, "200"):
            raise DependencyUnavailableError(
                "Fawry refused the request", dependency="fawry",
                details={"status_code": status_code, "description": str((data or {}).get("statusDescription", ""))[:200]},
            )
        return data
