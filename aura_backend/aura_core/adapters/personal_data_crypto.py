"""The ``gcm2`` field format shared by the server and the Flutter client (ADR-032).

::

    gcm2:<kid>:<base64 12-byte IV>:<base64 ciphertext || 16-byte GCM tag>

AES-256-GCM over the UTF-8 plaintext with the user's data key named by ``kid``, no associated
data -- byte-compatible with ``aura_client``'s ``EncryptionService`` (``encrypt`` package, GCM,
128-bit tag), which is pinned by fixed vectors on both sides
(``tests/unit/test_personal_data_crypto.py``, ``aura_client/test/data_key_crypto_test.dart``).

**Server-readable by design.** The data key is held by the server (wrapped by
``AURA_DATA_ENCRYPTION_KEYS``) so that the agent can read and write these fields. This is
encryption at rest and in transit between the user's own devices and the server, not end-to-end
encryption.

The legacy ``gcm:<iv>:<ct>`` format was sealed with a key only one device held. The server has
never seen any such key, so a legacy value is *unreadable* here: :func:`decrypt_field` returns
``None`` for it, and callers must not pass the raw string on as if it were the user's text.
"""

from __future__ import annotations

import base64
import binascii
import os
import re

from ..domain.errors import DataIntegrityError
from ..domain.user_keys import UserKeyring

__all__ = ["GCM2_PREFIX", "LEGACY_PREFIX", "encrypt_field", "decrypt_field", "is_gcm2"]

GCM2_PREFIX = "gcm2:"
LEGACY_PREFIX = "gcm:"
_IV_BYTES = 12
_TAG_BYTES = 16
_KID_RE = re.compile(r"^[0-9a-f]{8}$")


def is_gcm2(value: object) -> bool:
    return isinstance(value, str) and value.startswith(GCM2_PREFIX)


def encrypt_field(keyring: UserKeyring, plaintext: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key = keyring.primary
    iv = os.urandom(_IV_BYTES)
    sealed = AESGCM(key.key).encrypt(iv, plaintext.encode("utf-8"), None)
    return (
        f"{GCM2_PREFIX}{key.kid}:{base64.b64encode(iv).decode('ascii')}:"
        f"{base64.b64encode(sealed).decode('ascii')}"
    )


def decrypt_field(keyring: UserKeyring, stored: str) -> str | None:
    """The plaintext, or ``None`` when this server cannot read the value: a legacy ``gcm:``
    value, a ``gcm2`` value under a key this owner does not have, or anything else that is not a
    ``gcm2`` envelope (an ``*_enc`` column never legitimately holds plaintext).

    Raises :class:`DataIntegrityError` for a ``gcm2`` value under one of the owner's keys that
    fails authentication or is malformed -- tampering is reported, not silently hidden.
    """
    if stored == "":
        return ""  # a tombstone's blanked NOT NULL column
    if not is_gcm2(stored):
        return None
    parts = stored.split(":")
    if len(parts) != 4 or not _KID_RE.match(parts[1]):
        raise DataIntegrityError(
            "a stored gcm2 value is malformed", details={"reason": "malformed_envelope"}
        )
    kid = parts[1]
    key = keyring.get(kid)
    if key is None:
        return None
    try:
        iv = base64.b64decode(parts[2], validate=True)
        sealed = base64.b64decode(parts[3], validate=True)
    except (binascii.Error, ValueError):
        raise DataIntegrityError(
            "a stored gcm2 value is malformed",
            details={"reason": "malformed_envelope", "key_id": kid},
        ) from None
    if len(iv) != _IV_BYTES or len(sealed) < _TAG_BYTES:
        raise DataIntegrityError(
            "a stored gcm2 value is malformed",
            details={"reason": "malformed_envelope", "key_id": kid},
        )
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    try:
        plaintext = AESGCM(key.key).decrypt(iv, sealed, None)
    except InvalidTag:
        raise DataIntegrityError(
            "a stored gcm2 value failed authentication",
            details={"reason": "authentication_failed", "key_id": kid},
        ) from None
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError:
        raise DataIntegrityError(
            "a stored gcm2 value is not UTF-8",
            details={"reason": "malformed_payload", "key_id": kid},
        ) from None
