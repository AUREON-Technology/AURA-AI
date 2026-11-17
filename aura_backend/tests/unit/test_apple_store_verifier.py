"""SaaS P1-D2: App Store Server Notification V2 verification against a pinned root."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.x509.oid import NameOID, ObjectIdentifier

from aura_core.adapters.apple_store import (
    APPLE_ROOT_CA_G3_SHA256,
    INTERMEDIATE_OID,
    LEAF_OID,
    AppleNotificationVerifier,
    apple_root_ca_g3,
)
from aura_core.domain.errors import AuthenticationError, ValidationError

NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)
BUNDLE = "tech.aureon.app"
TOKEN = "7d1f4c9a-2b0e-4b4e-9f1a-0c5d2e6f8a11"


def _cert(subject: str, key, issuer_name, issuer_key, *, ca: bool, oid: str | None, start=None, end=None):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder().subject_name(name).issuer_name(issuer_name or name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(start or NOW - timedelta(days=365)).not_valid_after(end or NOW + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if oid:
        builder = builder.add_extension(x509.UnrecognizedExtension(ObjectIdentifier(oid), b"\x05\x00"), critical=False)
    return builder.sign(issuer_key or key, hashes.SHA256())


class Chain:
    def __init__(self, *, leaf_oid=LEAF_OID, intermediate_oid=INTERMEDIATE_OID, leaf_end=None) -> None:
        self.root_key = ec.generate_private_key(ec.SECP256R1())
        self.root = _cert("Test Root", self.root_key, None, None, ca=True, oid=None)
        self.int_key = ec.generate_private_key(ec.SECP256R1())
        self.intermediate = _cert("Test WWDR", self.int_key, self.root.subject, self.root_key, ca=True, oid=intermediate_oid)
        self.leaf_key = ec.generate_private_key(ec.SECP256R1())
        self.leaf = _cert("Test Leaf", self.leaf_key, self.intermediate.subject, self.int_key, ca=False, oid=leaf_oid,
                          end=leaf_end)

    @property
    def root_der(self) -> bytes:
        return self.root.public_bytes(serialization.Encoding.DER)

    def sign(self, claims: dict, *, key=None) -> str:
        x5c = [base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode()
               for c in (self.leaf, self.intermediate, self.root)]
        enc = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()  # noqa: E731
        head = enc(json.dumps({"alg": "ES256", "x5c": x5c}).encode())
        body = enc(json.dumps(claims).encode())
        der = (key or self.leaf_key).sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{head}.{body}.{enc(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def _transaction(**overrides) -> dict:
    tx = {"bundleId": BUNDLE, "environment": "Sandbox", "productId": "aura.pro.monthly",
          "originalTransactionId": "2000000001", "transactionId": "2000000009", "appAccountToken": TOKEN,
          "expiresDate": int((NOW + timedelta(days=30)).timestamp() * 1000)}
    tx.update(overrides)
    return tx


def _notification(chain: Chain, tx: dict | None = None, *, signed_tx: str | None = None, **overrides) -> str:
    data = {"bundleId": BUNDLE, "environment": "Sandbox"}
    if tx is not None or signed_tx is not None:
        data["signedTransactionInfo"] = signed_tx or chain.sign(tx)
    claims = {"notificationType": "DID_RENEW", "subtype": None, "notificationUUID": "n-1",
              "signedDate": int(NOW.timestamp() * 1000), "data": data}
    claims.update(overrides)
    return chain.sign(claims)


def _verifier(chain: Chain, **kwargs) -> AppleNotificationVerifier:
    return AppleNotificationVerifier(BUNDLE, root_der=chain.root_der, **kwargs)


def test_embedded_apple_root_matches_the_pin() -> None:
    der = apple_root_ca_g3()
    assert hashlib.sha256(der).hexdigest() == APPLE_ROOT_CA_G3_SHA256
    assert "Apple Root CA - G3" in x509.load_der_x509_certificate(der).subject.rfc4514_string()


async def test_good_chain_yields_the_event() -> None:
    chain = Chain()
    event = await _verifier(chain, environment="Sandbox").verify(_notification(chain, _transaction()))
    assert (event.event_id, event.type, event.subtype, event.bundle_id) == ("n-1", "DID_RENEW", None, BUNDLE)
    tx = event.transaction
    assert tx.app_account_token == TOKEN and tx.product_id == "aura.pro.monthly"
    assert tx.original_transaction_id == "2000000001" and tx.expires_at == NOW + timedelta(days=30)
    assert tx.revoked is False


async def test_revocation_is_reported() -> None:
    chain = Chain()
    event = await _verifier(chain).verify(_notification(chain, _transaction(revocationDate=1), notificationType="REFUND"))
    assert event.transaction.revoked is True


async def test_chain_under_another_root_is_refused() -> None:
    """Review focus #1: a perfectly formed chain that is not Apple's."""
    forged, pinned = Chain(), Chain()
    with pytest.raises(AuthenticationError):
        await _verifier(pinned).verify(_notification(forged, _transaction()))
    with pytest.raises(AuthenticationError):
        await AppleNotificationVerifier(BUNDLE).verify(_notification(forged, _transaction()))  # the real Apple pin


@pytest.mark.parametrize("missing", ["leaf", "intermediate"])
async def test_missing_apple_oid_is_refused(missing) -> None:
    chain = Chain(**{f"{missing}_oid": None})
    with pytest.raises(AuthenticationError):
        await _verifier(chain).verify(_notification(chain, _transaction()))


async def test_expired_leaf_is_refused() -> None:
    chain = Chain(leaf_end=NOW - timedelta(days=1))
    with pytest.raises(AuthenticationError):
        await _verifier(chain).verify(_notification(chain, _transaction()))


async def test_tampered_payload_is_refused() -> None:
    chain = Chain()
    head, body, sig = _notification(chain, _transaction()).split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["notificationType"] = "SUBSCRIBED"
    forged_body = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    with pytest.raises(AuthenticationError):
        await _verifier(chain).verify(f"{head}.{forged_body}.{sig}")


async def test_signed_by_a_key_that_is_not_the_leaf_is_refused() -> None:
    chain = Chain()
    with pytest.raises(AuthenticationError):
        await _verifier(chain).verify(chain.sign({"notificationUUID": "n"}, key=ec.generate_private_key(ec.SECP256R1())))


async def test_wrong_bundle_or_environment_is_refused() -> None:
    chain = Chain()
    with pytest.raises(ValidationError):
        await _verifier(chain).verify(_notification(chain, _transaction(bundleId="com.other.app")))
    with pytest.raises(ValidationError):
        await AppleNotificationVerifier("com.other.app", root_der=chain.root_der).verify(_notification(chain))
    with pytest.raises(ValidationError):
        await _verifier(chain, environment="Production").verify(_notification(chain, _transaction()))


async def test_nested_transaction_with_a_bad_signature_is_refused() -> None:
    chain, other = Chain(), Chain()
    with pytest.raises(AuthenticationError):
        await _verifier(chain).verify(_notification(chain, signed_tx=other.sign(_transaction())))


@pytest.mark.parametrize("payload", ["", "a.b", "a.b.c", "x" * 10, "eyJ9.eyJ9.AA"])
async def test_malformed_payloads_are_4xx_errors(payload) -> None:
    with pytest.raises((ValidationError, AuthenticationError)):
        await _verifier(Chain()).verify(payload)
