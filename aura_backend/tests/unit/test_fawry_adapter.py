"""SaaS P1-D1: the Fawry adapter speaks Fawry's wire format exactly (httpx.MockTransport).

Each expected signature is computed here from the documented formula, independently of the
adapter, so a change to concatenation order, amount formatting or the secret's position fails.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from aura_core.adapters.fawry import FawryPaymentProvider
from aura_core.domain.billing import Payment, PaymentStatus
from aura_core.domain.errors import AuthenticationError, DependencyUnavailableError, ValidationError

BASE = "https://atfawry.fawrystaging.com/ECommerceWeb/Fawry/"
MERCHANT, SECRET = "MERCH123", "s3cr3t-key"
T0 = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _payment(**overrides) -> Payment:
    fields = dict(id="pay-abc", workspace_id="w" * 32, plan_id="pro", seats=1, amount_piastres=15000,
                  method="PAYATFAWRY", purpose="initial", status=PaymentStatus.PENDING, provider_ref=None,
                  reference_number=None, created_at=T0, expires_at=T0 + timedelta(hours=72))
    fields.update(overrides)
    return Payment(**fields)


def _provider(handler) -> tuple[FawryPaymentProvider, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(record))
    return FawryPaymentProvider(BASE, MERCHANT, SECRET, http=client), seen


async def test_reference_charge_body_and_signature() -> None:
    provider, seen = _provider(lambda r: httpx.Response(200, json={
        "type": "ChargeResponse", "referenceNumber": "963455678", "statusCode": 200, "statusDescription": "Operation done successfully"}))
    charge = await provider.create_reference(_payment(), customer_profile_id="cp-1", email="ann@example.com")
    assert charge.reference_number == "963455678"
    request = seen[0]
    assert request.method == "POST" and str(request.url) == BASE + "payments/charge"
    body = json.loads(request.content)
    assert body["merchantCode"] == MERCHANT and body["merchantRefNum"] == "pay-abc"
    assert body["paymentMethod"] == "PAYATFAWRY" and body["amount"] == "150.00" and body["currencyCode"] == "EGP"
    assert body["chargeItems"][0]["price"] == "150.00" and body["chargeItems"][0]["quantity"] == 1
    assert body["paymentExpiry"] == int((T0 + timedelta(hours=72)).timestamp() * 1000)
    assert body["signature"] == _sha(f"{MERCHANT}pay-abccp-1PAYATFAWRY150.00{SECRET}")
    assert SECRET not in request.content.decode()


async def test_card_charge_signature_includes_the_token() -> None:
    provider, seen = _provider(lambda r: httpx.Response(200, json={"statusCode": 200, "referenceNumber": "1", "fawryRefNumber": "F1"}))
    await provider.charge_card(_payment(method="CARD", amount_piastres=4999), customer_profile_id="cp-1", card_token="tok-9")
    body = json.loads(seen[0].content)
    assert body["paymentMethod"] == "CARD" and body["cardToken"] == "tok-9" and body["amount"] == "49.99"
    assert body["signature"] == _sha(f"{MERCHANT}pay-abccp-1CARD49.99tok-9{SECRET}")


async def test_status_query_signature_and_mapping() -> None:
    provider, seen = _provider(lambda r: httpx.Response(200, json={
        "statusCode": 200, "paymentStatus": "PAID", "paymentAmount": 150, "fawryRefNumber": "FR-1"}))
    status = await provider.status("pay-abc")
    assert (status.status, status.amount_piastres, status.provider_ref) == (PaymentStatus.PAID, 15000, "FR-1")
    params = dict(seen[0].url.params)
    assert seen[0].method == "GET" and str(seen[0].url).startswith(BASE + "payments/status")
    assert params["merchantRefNumber"] == "pay-abc" and params["signature"] == _sha(f"{MERCHANT}pay-abc{SECRET}")


@pytest.mark.parametrize("fawry, ours", [
    ("PAID", PaymentStatus.PAID), ("UNPAID", PaymentStatus.PENDING), ("NEW", PaymentStatus.PENDING),
    ("EXPIRED", PaymentStatus.EXPIRED), ("CANCELED", PaymentStatus.FAILED), ("CANCELLED", PaymentStatus.FAILED),
    ("FAILED", PaymentStatus.FAILED), ("REFUNDED", PaymentStatus.REFUNDED),
])
async def test_status_values(fawry, ours) -> None:
    provider, _ = _provider(lambda r: httpx.Response(200, json={"statusCode": 200, "orderStatus": fawry, "paymentAmount": 1}))
    assert (await provider.status("p")).status is ours


async def test_error_status_code_is_retryable_and_leaks_no_secret() -> None:
    provider, _ = _provider(lambda r: httpx.Response(200, json={"statusCode": 9901, "statusDescription": "Invalid signature"}))
    with pytest.raises(DependencyUnavailableError) as raised:
        await provider.create_reference(_payment(), customer_profile_id="cp", email=None)
    assert SECRET not in repr(raised.value.to_dict())


def _notification(**overrides) -> dict:
    note = {"fawryRefNumber": "FR-77", "merchantRefNum": "pay-abc", "paymentAmount": 150, "orderAmount": 150,
            "orderStatus": "PAID", "paymentMethod": "PAYATFAWRY", "paymentRefrenceNumber": "963455678"}
    note.update(overrides)
    note["messageSignature"] = _sha(
        f"{note['fawryRefNumber']}{note['merchantRefNum']}{float(note['paymentAmount']):.2f}{float(note['orderAmount']):.2f}"
        f"{note['orderStatus']}{note['paymentMethod']}{note.get('paymentRefrenceNumber') or ''}{SECRET}"
    )
    return note


async def test_notification_good_signature() -> None:
    provider, _ = _provider(lambda r: httpx.Response(500))
    parsed = await provider.parse_notification(json.dumps(_notification()).encode())
    assert (parsed.payment_id, parsed.status, parsed.amount_piastres, parsed.provider_ref) == ("pay-abc", PaymentStatus.PAID, 15000, "FR-77")


async def test_notification_amount_formatting_and_missing_reference() -> None:
    provider, _ = _provider(lambda r: httpx.Response(500))
    note = _notification(paymentAmount="150", orderAmount=150.0, paymentRefrenceNumber=None)
    parsed = await provider.parse_notification(json.dumps(note).encode())
    assert parsed.amount_piastres == 15000


async def test_notification_bad_signature_and_malformed() -> None:
    provider, _ = _provider(lambda r: httpx.Response(500))
    tampered = _notification()
    tampered["paymentAmount"] = 1
    with pytest.raises(AuthenticationError):
        await provider.parse_notification(json.dumps(tampered).encode())
    for body in (b"not json", b"[]", json.dumps({"merchantRefNum": "x"}).encode()):
        with pytest.raises(ValidationError):
            await provider.parse_notification(body)
