"""App Attest cryptographic verify with a mocked Apple client."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import cbor2
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from services.attestation.app_attest import APP_ATTEST_NONCE_OID, verify_app_attest

TEAM_ID = "TEAMID12"
BUNDLE_ID = "com.auraai.auraClient"


class _MockAppleClient:
    def __init__(self, root_pem: bytes):
        self._root_pem = root_pem

    def load_app_attest_roots(self) -> list[bytes]:
        return [self._root_pem]


def _build_ca_and_leaf(auth_data: bytes, challenge: str):
    ca_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)

    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test App Attest Root")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(1)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    client_data_hash = hashlib.sha256(challenge.encode("utf-8")).digest()
    nonce = hashlib.sha256(auth_data + client_data_hash).digest()
    leaf_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test App Attest Leaf")])
    leaf_cert = (
        x509.CertificateBuilder()
        .subject_name(leaf_name)
        .issuer_name(ca_name)
        .public_key(leaf_key.public_key())
        .serial_number(2)
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=30))
        .add_extension(x509.UnrecognizedExtension(APP_ATTEST_NONCE_OID, nonce), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return ca_cert, leaf_cert, leaf_key


def _auth_data() -> bytes:
    rp_id_hash = hashlib.sha256(f"{TEAM_ID}.{BUNDLE_ID}".encode("utf-8")).digest()
    return rp_id_hash + b"\x40" + b"\x00\x00\x00\x00"


def _envelope(leaf_der: bytes, ca_der: bytes, auth_data: bytes, challenge: str, assertion: bytes | None = None) -> str:
    attestation = {
        "fmt": "apple-appattest",
        "attStmt": {"x5c": [leaf_der, ca_der], "receipt": b""},
        "authData": auth_data,
    }
    payload = {
        "format": "apple-appattest",
        "key_id": "test-key",
        "attestation": base64.b64encode(cbor2.dumps(attestation)).decode("ascii"),
        "challenge": challenge,
    }
    if assertion is not None:
        payload["assertion"] = base64.b64encode(assertion).decode("ascii")
    return json.dumps(payload)


@pytest.fixture
def valid_ios_fixture():
    challenge = "server-challenge-1"
    auth_data = _auth_data()
    ca_cert, leaf_cert, leaf_key = _build_ca_and_leaf(auth_data, challenge)
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)
    token = _envelope(
        leaf_cert.public_bytes(serialization.Encoding.DER),
        ca_cert.public_bytes(serialization.Encoding.DER),
        auth_data,
        challenge,
    )
    return token, _MockAppleClient(ca_pem), challenge, leaf_key, auth_data


def test_valid_shaped_fixture_accepted_with_mocked_apple_client(valid_ios_fixture):
    token, apple_client, challenge, _, _ = valid_ios_fixture
    assert verify_app_attest(
        token,
        bundle_id=BUNDLE_ID,
        team_id=TEAM_ID,
        apple_client=apple_client,
        expected_nonce=challenge,
    )


def test_fake_tokens_rejected_even_with_mocked_apple_client(valid_ios_fixture):
    _, apple_client, challenge, _, _ = valid_ios_fixture
    kwargs = dict(
        bundle_id=BUNDLE_ID,
        team_id=TEAM_ID,
        apple_client=apple_client,
        expected_nonce=challenge,
    )
    assert verify_app_attest("not-json-and-not-cbor", **kwargs) is False
    assert verify_app_attest("ios_app_attest_mock_assertion_token_string", **kwargs) is False
    assert verify_app_attest(base64.b64encode(b"looks_valid_device_blob").decode(), **kwargs) is False
    assert verify_app_attest('{"format":"apple-appattest","attestation":"dGVzdA=="}', **kwargs) is False


def test_wrong_app_id_rejected(valid_ios_fixture):
    token, apple_client, challenge, _, _ = valid_ios_fixture
    assert verify_app_attest(
        token,
        bundle_id="com.evil.app",
        team_id=TEAM_ID,
        apple_client=apple_client,
        expected_nonce=challenge,
    ) is False


def test_empty_apple_roots_fail_closed(valid_ios_fixture):
    token, _, challenge, _, _ = valid_ios_fixture
    empty = MagicMock()
    empty.load_app_attest_roots.return_value = []
    assert verify_app_attest(
        token,
        bundle_id=BUNDLE_ID,
        team_id=TEAM_ID,
        apple_client=empty,
        expected_nonce=challenge,
    ) is False


def test_assertion_signed_by_leaf_is_accepted(valid_ios_fixture):
    token, apple_client, challenge, leaf_key, auth_data = valid_ios_fixture
    client_data_hash = hashlib.sha256(challenge.encode("utf-8")).digest()
    signed = auth_data + client_data_hash
    signature = leaf_key.sign(signed, ec.ECDSA(hashes.SHA256()))
    assertion = cbor2.dumps({"authenticatorData": auth_data, "signature": signature})

    envelope = json.loads(token)
    envelope["assertion"] = base64.b64encode(assertion).decode("ascii")
    assert verify_app_attest(
        json.dumps(envelope),
        bundle_id=BUNDLE_ID,
        team_id=TEAM_ID,
        apple_client=apple_client,
        expected_nonce=challenge,
    )


def test_missing_team_id_fails_closed(valid_ios_fixture):
    token, apple_client, challenge, _, _ = valid_ios_fixture
    assert verify_app_attest(
        token,
        bundle_id=BUNDLE_ID,
        team_id="",
        apple_client=apple_client,
        expected_nonce=challenge,
    ) is False
