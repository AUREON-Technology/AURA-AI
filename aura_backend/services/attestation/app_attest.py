"""Cryptographic verification of Apple App Attest objects.

The client sends a JSON envelope::

    {
      "format": "apple-appattest",
      "key_id": "...",
      "attestation": "<base64 CBOR>",
      "assertion": "<base64 CBOR, optional>",
      "challenge": "..."
    }

Verification is fail-closed: missing fields, a chain that does not terminate
at a root from ``AppleAppAttestClient``, an app-ID mismatch, or a nonce
mismatch all return False. Tests inject a mocked Apple client whose roots
are a fixture CA.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any, Optional

import cbor2
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.x509.oid import ObjectIdentifier

from services.attestation.apple_client import AppleAppAttestClient, default_apple_client

logger = logging.getLogger("aura_backend.security.app_attest")

APP_ATTEST_NONCE_OID = ObjectIdentifier("1.2.840.113635.100.8.2")


def verify_app_attest(
    attestation_token: str,
    *,
    bundle_id: str,
    team_id: str,
    apple_client: Optional[AppleAppAttestClient] = None,
    expected_nonce: Optional[str] = None,
) -> bool:
    try:
        envelope = _parse_envelope(attestation_token)
        if envelope is None:
            logger.warning("iOS App Attest failure: token is not a JSON envelope or CBOR blob.")
            return False

        attestation_raw = envelope.get("attestation")
        if not attestation_raw:
            logger.warning("iOS App Attest failure: missing attestation object.")
            return False

        attestation_bytes = _b64(attestation_raw)
        if not attestation_bytes:
            logger.warning("iOS App Attest failure: attestation is not valid base64.")
            return False

        try:
            attestation = cbor2.loads(attestation_bytes)
        except Exception as err:
            logger.warning("iOS App Attest failure: attestation is not CBOR: %s", err)
            return False

        if not isinstance(attestation, dict):
            logger.warning("iOS App Attest failure: attestation CBOR is not a map.")
            return False

        fmt = attestation.get("fmt") or attestation.get(b"fmt")
        if _as_str(fmt) != "apple-appattest":
            logger.warning("iOS App Attest failure: unexpected fmt %r", fmt)
            return False

        att_stmt = attestation.get("attStmt") or attestation.get(b"attStmt") or {}
        auth_data = attestation.get("authData") or attestation.get(b"authData")
        if not isinstance(att_stmt, dict) or not isinstance(auth_data, (bytes, bytearray)):
            logger.warning("iOS App Attest failure: missing attStmt or authData.")
            return False
        auth_data = bytes(auth_data)

        x5c = att_stmt.get("x5c") or att_stmt.get(b"x5c") or []
        if not x5c:
            logger.warning("iOS App Attest failure: empty x5c chain.")
            return False

        certs = [_load_cert(item) for item in x5c]
        if any(cert is None for cert in certs):
            logger.warning("iOS App Attest failure: x5c contains an unreadable certificate.")
            return False

        client = apple_client or default_apple_client()
        roots = [_load_cert(item) for item in client.load_app_attest_roots()]
        roots = [cert for cert in roots if cert is not None]
        if not roots:
            logger.warning("iOS App Attest failure: no App Attest roots from Apple client.")
            return False

        if not _chain_trusts_root(certs, roots):  # type: ignore[arg-type]
            logger.warning("iOS App Attest failure: certificate chain does not trust Apple root.")
            return False

        leaf: x509.Certificate = certs[0]  # type: ignore[assignment]
        if not _app_id_matches(auth_data, team_id, bundle_id):
            logger.warning("iOS App Attest failure: rpIdHash does not match app ID.")
            return False

        challenge = envelope.get("challenge") or expected_nonce
        if expected_nonce and envelope.get("challenge") and envelope["challenge"] != expected_nonce:
            logger.warning("iOS App Attest failure: envelope challenge does not match expected nonce.")
            return False
        if challenge and not _nonce_matches(leaf, auth_data, str(challenge)):
            logger.warning("iOS App Attest failure: nonce extension mismatch.")
            return False

        assertion_raw = envelope.get("assertion")
        if assertion_raw:
            assertion_bytes = _b64(assertion_raw)
            if not assertion_bytes:
                logger.warning("iOS App Attest failure: assertion is not valid base64.")
                return False
            if not _verify_assertion(assertion_bytes, leaf, str(challenge or "")):
                logger.warning("iOS App Attest failure: assertion signature invalid.")
                return False

        logger.info("iOS App Attest cryptographic verification succeeded.")
        return True
    except Exception as err:
        logger.error("iOS App Attest verification error: %s", err)
        return False


def _parse_envelope(token: str) -> Optional[dict[str, Any]]:
    raw = (token or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return data
    decoded = _b64(raw)
    if decoded is None:
        return None
    return {"format": "apple-appattest", "attestation": raw}


def _b64(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if not isinstance(value, str) or not value:
        return None
    pad = "=" * (-len(value) % 4)
    try:
        return base64.b64decode(value + pad)
    except Exception:
        try:
            return base64.urlsafe_b64decode(value + pad)
        except Exception:
            return None


def _as_str(value: Any) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value) if value is not None else ""


def _load_cert(item: Any) -> Optional[x509.Certificate]:
    blob = item if isinstance(item, (bytes, bytearray)) else _b64(item)
    if blob is None:
        if isinstance(item, str) and "BEGIN CERTIFICATE" in item:
            blob = item.encode("ascii")
        else:
            return None
    data = bytes(blob)
    try:
        if b"BEGIN CERTIFICATE" in data:
            return x509.load_pem_x509_certificate(data)
        return x509.load_der_x509_certificate(data)
    except Exception:
        return None


def _verify_cert_signature(cert: x509.Certificate, issuer: x509.Certificate) -> bool:
    public_key = issuer.public_key()
    try:
        hash_algo = cert.signature_hash_algorithm
        if hash_algo is None:
            return False
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                ec.ECDSA(hash_algo),
            )
            return True
        if isinstance(public_key, rsa.RSAPublicKey):
            public_key.verify(
                cert.signature,
                cert.tbs_certificate_bytes,
                padding.PKCS1v15(),
                hash_algo,
            )
            return True
    except (InvalidSignature, TypeError, ValueError):
        return False
    return False


def _chain_trusts_root(certs: list[x509.Certificate], roots: list[x509.Certificate]) -> bool:
    if not certs:
        return False
    for index in range(len(certs) - 1):
        if not _verify_cert_signature(certs[index], certs[index + 1]):
            return False
    tail = certs[-1]
    for root in roots:
        if tail.tbs_certificate_bytes == root.tbs_certificate_bytes:
            return True
        if _verify_cert_signature(tail, root):
            return True
    return False


def _app_id_matches(auth_data: bytes, team_id: str, bundle_id: str) -> bool:
    if len(auth_data) < 32:
        return False
    if not team_id or not bundle_id:
        return False
    app_id = f"{team_id}.{bundle_id}".encode("utf-8")
    expected = hashlib.sha256(app_id).digest()
    return auth_data[:32] == expected


def _nonce_matches(leaf: x509.Certificate, auth_data: bytes, challenge: str) -> bool:
    client_data_hash = hashlib.sha256(challenge.encode("utf-8")).digest()
    expected = hashlib.sha256(auth_data + client_data_hash).digest()
    try:
        ext = leaf.extensions.get_extension_for_oid(APP_ATTEST_NONCE_OID)
    except x509.ExtensionNotFound:
        return False
    value = ext.value
    raw = value.value if isinstance(value, x509.UnrecognizedExtension) else bytes(value)
    return expected in raw


def _verify_assertion(assertion_bytes: bytes, leaf: x509.Certificate, challenge: str) -> bool:
    try:
        assertion = cbor2.loads(assertion_bytes)
    except Exception:
        return False
    if not isinstance(assertion, dict):
        return False
    authenticator_data = assertion.get("authenticatorData") or assertion.get(b"authenticatorData")
    signature = assertion.get("signature") or assertion.get(b"signature")
    if not isinstance(authenticator_data, (bytes, bytearray)) or not isinstance(signature, (bytes, bytearray)):
        return False
    client_data_hash = hashlib.sha256(challenge.encode("utf-8")).digest()
    signed = bytes(authenticator_data) + client_data_hash
    public_key = leaf.public_key()
    try:
        if isinstance(public_key, ec.EllipticCurvePublicKey):
            public_key.verify(bytes(signature), signed, ec.ECDSA(hashes.SHA256()))
            return True
    except InvalidSignature:
        return False
    return False
