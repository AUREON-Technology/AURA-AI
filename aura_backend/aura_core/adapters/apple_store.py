"""Apple App Store Server Notifications V2, verified (SaaS P1-D2).

Apple POSTs ``{"signedPayload": "<JWS>"}``. The JWS is ES256 and carries its certificate chain in
the header's ``x5c`` (leaf, intermediate, root). It is trusted only when:

* the chain's last certificate **is** the pinned Apple Root CA - G3 (byte-for-byte; a chain under
  any other root is refused however well-formed it is -- review focus #1);
* each certificate is directly issued and signed by the next, and every one is valid at the
  payload's ``signedDate`` (Apple re-sends old notifications; ``signedDate`` falling back to now);
* the leaf carries Apple's receipt-signing OID ``1.2.840.113635.100.6.11.1`` and the intermediate
  Apple's WWDR OID ``1.2.840.113635.100.6.2.1``, and the intermediate is a CA;
* the ES256 signature verifies with the leaf's P-256 key.

The nested ``data.signedTransactionInfo`` is a JWS of its own and goes through the same checks.
The bundle id (and, when configured, the environment) must be this app's.

``cryptography`` is imported inside functions (adapters import vendor libraries lazily).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from ..domain.errors import AuthenticationError, ValidationError

__all__ = [
    "APPLE_ROOT_CA_G3_SHA256",
    "AppleEvent",
    "AppleNotificationVerifier",
    "AppleTransaction",
    "apple_root_ca_g3",
]

APPLE_ROOT_CA_G3_SHA256 = "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179"
LEAF_OID = "1.2.840.113635.100.6.11.1"
INTERMEDIATE_OID = "1.2.840.113635.100.6.2.1"

#: Apple Root CA - G3, DER, from https://www.apple.com/certificateauthority/AppleRootCA-G3.cer
#: (valid 2014-04-30 .. 2039-04-30). ``apple_root_ca_g3()`` refuses it if it ever stops hashing to
#: the pinned value above.
_APPLE_ROOT_CA_G3_B64 = (
    "MIICQzCCAcmgAwIBAgIILcX8iNLFS5UwCgYIKoZIzj0EAwMwZzEbMBkGA1UEAwwSQXBwbGUgUm9v"
    "dCBDQSAtIEczMSYwJAYDVQQLDB1BcHBsZSBDZXJ0aWZpY2F0aW9uIEF1dGhvcml0eTETMBEGA1UE"
    "CgwKQXBwbGUgSW5jLjELMAkGA1UEBhMCVVMwHhcNMTQwNDMwMTgxOTA2WhcNMzkwNDMwMTgxOTA2"
    "WjBnMRswGQYDVQQDDBJBcHBsZSBSb290IENBIC0gRzMxJjAkBgNVBAsMHUFwcGxlIENlcnRpZmlj"
    "YXRpb24gQXV0aG9yaXR5MRMwEQYDVQQKDApBcHBsZSBJbmMuMQswCQYDVQQGEwJVUzB2MBAGByqG"
    "SM49AgEGBSuBBAAiA2IABJjpLz1AcqTtkyJygRMc3RCV8cWjTnHcFBbZDuWmBSp3ZHtfTjjTuxxE"
    "tX/1H7YyYl3J6YRbTzBPEVoA/VhYDKX1DyxNB0cTddqXl5dvMVztK517IDvYuVTZXpmkOlEKMaNC"
    "MEAwHQYDVR0OBBYEFLuw3qFYM4iapIqZ3r6966/ayySrMA8GA1UdEwEB/wQFMAMBAf8wDgYDVR0P"
    "AQH/BAQDAgEGMAoGCCqGSM49BAMDA2gAMGUCMQCD6cHEFl4aXTQY2e3v9GwOAEZLuN+yRhHFD/3m"
    "eoyhpmvOwgPUnPWTxnS4at+qIxUCMG1mihDK1A3UT82NQz60imOlM27jbdoXt2QfyFMm+YhidDkL"
    "F1vLUagM6BgD56KyKA=="
)


def apple_root_ca_g3() -> bytes:
    der = base64.b64decode(_APPLE_ROOT_CA_G3_B64)
    if hashlib.sha256(der).hexdigest() != APPLE_ROOT_CA_G3_SHA256:  # pragma: no cover - tamper guard
        raise RuntimeError("embedded Apple root certificate does not match its pin")
    return der


@dataclass(frozen=True, slots=True)
class AppleTransaction:
    app_account_token: str | None
    product_id: str
    original_transaction_id: str
    transaction_id: str
    expires_at: datetime | None
    revoked: bool


@dataclass(frozen=True, slots=True)
class AppleEvent:
    event_id: str
    type: str
    subtype: str | None
    bundle_id: str
    environment: str
    signed_at: datetime | None
    transaction: AppleTransaction | None


def _b64url(part: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
    except (binascii.Error, ValueError) as exc:
        raise ValidationError("signed payload is not base64url") from exc


def _json(raw: bytes, what: str) -> dict:
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise ValidationError(f"{what} is not JSON") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"{what} is not an object")
    return value


def _ms(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("timestamp is not a number")
    try:
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError) as exc:  # 1e30, inf, nan: refused, not a 500
        raise ValidationError("timestamp is out of range") from exc


class AppleNotificationVerifier:
    """``root_der`` defaults to the pinned Apple Root CA - G3; tests pass their own root."""

    def __init__(self, bundle_id: str, *, environment: str | None = None, root_der: bytes | None = None,
                 clock: Any = None) -> None:
        if not bundle_id:
            raise ValueError("bundle_id is required")
        self._bundle_id = bundle_id
        self._environment = environment or None
        self._root = root_der if root_der is not None else apple_root_ca_g3()
        self._clock = clock

    def _now(self) -> datetime:
        return self._clock.now() if self._clock is not None else datetime.now(timezone.utc)

    async def verify(self, signed_payload: str) -> AppleEvent:
        claims = self._verify_jws(signed_payload)
        data = claims.get("data") if isinstance(claims.get("data"), dict) else {}
        event_id = claims.get("notificationUUID")
        kind = claims.get("notificationType")
        if not isinstance(event_id, str) or not event_id or not isinstance(kind, str) or not kind:
            raise ValidationError("notification has no notificationUUID or notificationType")
        bundle_id = str(data.get("bundleId") or "")
        environment = str(data.get("environment") or "")
        self._check_app(bundle_id, environment)
        transaction = None
        signed_tx = data.get("signedTransactionInfo")
        if signed_tx is not None:
            if not isinstance(signed_tx, str):
                raise ValidationError("signedTransactionInfo is not a string")
            tx = self._verify_jws(signed_tx)
            self._check_app(str(tx.get("bundleId") or ""), str(tx.get("environment") or environment))
            product_id, original, tx_id = tx.get("productId"), tx.get("originalTransactionId"), tx.get("transactionId")
            if not all(isinstance(v, str) and v for v in (product_id, original, tx_id)):
                raise ValidationError("transaction is missing its ids")
            token = tx.get("appAccountToken")
            transaction = AppleTransaction(
                app_account_token=str(token).lower() if isinstance(token, str) and token else None,
                product_id=product_id, original_transaction_id=original, transaction_id=tx_id,
                expires_at=_ms(tx.get("expiresDate")),
                revoked=tx.get("revocationDate") is not None,
            )
        subtype = claims.get("subtype")
        return AppleEvent(
            event_id=event_id, type=kind, subtype=subtype if isinstance(subtype, str) and subtype else None,
            bundle_id=bundle_id, environment=environment, signed_at=_ms(claims.get("signedDate")),
            transaction=transaction,
        )

    def _check_app(self, bundle_id: str, environment: str) -> None:
        if bundle_id != self._bundle_id:
            raise ValidationError("notification is for another app")
        if self._environment is not None and environment != self._environment:
            raise ValidationError("notification is for another App Store environment")

    def _verify_jws(self, token: str) -> dict:
        from cryptography import x509
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

        if not isinstance(token, str) or token.count(".") != 2:
            raise ValidationError("signed payload is not a compact JWS")
        header_b64, payload_b64, signature_b64 = token.split(".")
        header = _json(_b64url(header_b64), "JWS header")
        claims = _json(_b64url(payload_b64), "JWS payload")
        if header.get("alg") != "ES256":
            raise AuthenticationError("signed payload is not ES256")
        x5c = header.get("x5c")
        if not isinstance(x5c, list) or len(x5c) != 3 or not all(isinstance(c, str) for c in x5c):
            raise AuthenticationError("signed payload has no three-certificate chain")
        try:
            ders = [base64.b64decode(c, validate=True) for c in x5c]
            certs = [x509.load_der_x509_certificate(der) for der in ders]
        except (binascii.Error, ValueError) as exc:
            raise AuthenticationError("certificate chain is malformed") from exc
        leaf, intermediate, root = certs
        if ders[2] != self._root:
            raise AuthenticationError("certificate chain does not end in the Apple root")
        at = _ms(claims.get("signedDate")) or self._now()
        for cert in certs:
            if not (cert.not_valid_before_utc <= at <= cert.not_valid_after_utc):
                raise AuthenticationError("a certificate in the chain is not valid at the signing date")
        try:
            leaf.verify_directly_issued_by(intermediate)
            intermediate.verify_directly_issued_by(root)
        except (InvalidSignature, ValueError, TypeError) as exc:
            raise AuthenticationError("certificate chain does not verify") from exc
        if not _has_oid(leaf, LEAF_OID) or not _has_oid(intermediate, INTERMEDIATE_OID):
            raise AuthenticationError("certificate chain is not Apple's App Store chain")
        try:
            constraints = intermediate.extensions.get_extension_for_class(x509.BasicConstraints).value
        except x509.ExtensionNotFound as exc:
            raise AuthenticationError("intermediate certificate is not a CA") from exc
        if not constraints.ca:
            raise AuthenticationError("intermediate certificate is not a CA")
        key = leaf.public_key()
        if not isinstance(key, ec.EllipticCurvePublicKey) or key.curve.name != "secp256r1":
            raise AuthenticationError("leaf key is not P-256")
        raw = _b64url(signature_b64)
        if len(raw) != 64:
            raise AuthenticationError("signature is not a raw ES256 signature")
        signature = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        try:
            key.verify(signature, f"{header_b64}.{payload_b64}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise AuthenticationError("signed payload signature does not verify") from exc
        return claims


def _has_oid(cert: Any, dotted: str) -> bool:
    return any(ext.oid.dotted_string == dotted for ext in cert.extensions)
