"""Application-level authenticated encryption at rest for sensitive Postgres columns.

**Server-side encryption, not end-to-end encryption.** The gateway holds the key, encrypts a
field just before it is written and decrypts it just after it is read, so anyone who obtains a
database dump, a backup file, or read access to the Postgres volume sees ciphertext -- but the
running server (and anyone who controls it) reads everything, because it has to: memory recall
and the personal-OS tools feed these values to the model.

Since ADR-032 these keys are also the key-encryption key for each user's data key
(``user_data_keys.py``, table ``aura_user_data_keys``), which seals the synced tables' ``*_enc``
fields (``tasks``/``calendar_events``/``finance_records``/``health_records``/...). Those tables
are therefore server-readable too -- not zero-knowledge -- and whoever holds these keys and a
database dump can read them. Rotating these keys re-wraps the data keys
(``scripts/reencrypt_fields.py``); the synced ciphertext itself is untouched.

What is encrypted, and what deliberately is not, is listed in
``docs/architecture/004-memory-taxonomy.md`` ("Encryption at rest"); the short version is that
row identity and every column a query filters, sorts, or joins on (ids, ``owner_id``, ``kind``,
timestamps, memory ``subject``, provenance) stays in the clear, and only the free-text payload is
sealed.

**Why a second key rather than reusing ``AURA_SECRETS_ENCRYPTION_KEY``.** That key
(``secrets_crypto.py``) protects third-party credentials, whose blast radius on loss is "every
user reconnects GitHub". Losing *this* key loses every user's memories and records, so the two
must be rotatable, backed up, and reasoned about independently. The primitive is the same --
Fernet (AES-128-CBC + HMAC-SHA256) from the already-required ``cryptography`` package -- and the
same no-new-dependency reasoning applies.

Envelope (``v1``)::

    aura-enc:v1:<key id>:<fernet token>

* ``aura-enc:v1:`` is the version marker. A stored value without it is *legacy plaintext* and is
  returned unchanged, which is what makes the migration lazy: rows written before a key was
  configured stay readable and are re-encrypted by the next write (or in bulk by
  ``scripts/reencrypt_fields.py``).
* ``<key id>`` is 8 hex characters of a domain-separated SHA-256 of the key -- not secret, and
  it lets decryption pick the right key directly and lets the re-encryption script find rows
  sealed under a retired key with a plain ``NOT LIKE`` rather than decrypting every row.
* The Fernet plaintext is ``<len(context)>:<context><value>``. ``context`` names the row the value
  belongs to (table, id, owner), so a ciphertext copied into a *different* row -- by someone with
  database write access but no key -- fails authentication instead of silently moving one
  user's memory into another user's account. Fernet has no associated-data slot; binding the
  context inside the authenticated plaintext gets the same property.

Key rotation: ``AURA_DATA_ENCRYPTION_KEYS`` is a comma/whitespace-separated list, **newest
first**. The first key encrypts; every key decrypts. Rotating is: prepend a new key, restart,
run ``scripts/reencrypt_fields.py``, then drop the old key once it reports nothing left under it.

Tamper detection: a value that carries the envelope but fails authentication (modified,
truncated, moved to another row, or sealed under a key that is no longer configured) raises
:class:`~aura_core.domain.errors.DataIntegrityError`. It never falls back to returning the raw
stored string -- that would hand ciphertext to the model as if it were the user's words.

The key is not read here. ``container.py`` reads the environment and passes the keys in; this
module only knows how to use them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping, Sequence

from ..domain.errors import ConfigurationError, DataIntegrityError

__all__ = [
    "ENVELOPE_PREFIX",
    "JSON_ENVELOPE_KEY",
    "FieldCipher",
    "key_id",
]

#: Version marker every encrypted value starts with. A new format gets ``v2``; ``v1`` values must
#: stay decryptable for as long as any row might still hold one.
ENVELOPE_PREFIX = "aura-enc:v1:"

#: A JSONB column holding an encrypted mapping stores exactly ``{JSON_ENVELOPE_KEY: <envelope>}``,
#: so the column keeps its type (and its ``NOT NULL DEFAULT '{}'``) and no schema change is needed.
JSON_ENVELOPE_KEY = "_aura_enc"

_KEY_ENV = "AURA_DATA_ENCRYPTION_KEYS"
_KID_RE = re.compile(r"^[0-9a-f]{8}$")


def key_id(key: str) -> str:
    """Short, non-secret identifier for a key (domain-separated so it is not a bare key hash)."""
    return hashlib.sha256(b"aura-field-key-id\x00" + key.encode("ascii")).hexdigest()[:8]


class FieldCipher:
    """Encrypts and decrypts individual column values.

    Constructed with no keys it is a *passthrough*: writes store plaintext and reads return
    plaintext unchanged. That is the dev/test mode; ``container.py`` refuses to build one in
    staging/production. A passthrough cipher that reads an encrypted value raises
    :class:`ConfigurationError` rather than returning ciphertext.
    """

    def __init__(self, keys: Sequence[str] = (), *, setting: str = _KEY_ENV) -> None:
        #: The environment variable the keys came from, named in every error so an operator
        #: fixes the right one (the connector vault uses this class with a different key).
        self._setting = setting
        self._fernets: list[tuple[str, Any]] = []
        seen: set[str] = set()
        for position, key in enumerate(keys):
            kid = key_id(key)
            if kid in seen:
                raise ConfigurationError(
                    f"{setting} lists the same key twice (position {position})",
                    details={"setting": setting},
                )
            seen.add(kid)
            self._fernets.append((kid, self._build_fernet(key, position, setting)))

    @staticmethod
    def _build_fernet(key: str, position: int, setting: str = _KEY_ENV) -> Any:
        from cryptography.fernet import Fernet

        try:
            return Fernet(key.encode("ascii"))
        except Exception as exc:
            raise ConfigurationError(
                f"{setting} entry {position} is not a valid Fernet key (generate one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                'print(Fernet.generate_key().decode())")',
                details={"setting": setting, "position": position},
            ) from exc

    @classmethod
    def disabled(cls) -> "FieldCipher":
        return cls(())

    # -- introspection ----------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._fernets)

    @property
    def primary_key_id(self) -> str | None:
        return self._fernets[0][0] if self._fernets else None

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(kid for kid, _ in self._fernets)

    @staticmethod
    def is_encrypted(value: Any) -> bool:
        return isinstance(value, str) and value.startswith(ENVELOPE_PREFIX)

    @classmethod
    def is_encrypted_mapping(cls, value: Any) -> bool:
        return (
            isinstance(value, Mapping)
            and len(value) == 1
            and cls.is_encrypted(value.get(JSON_ENVELOPE_KEY))
        )

    def needs_reencryption(self, value: Any) -> bool:
        """True for a value the current configuration would store differently: legacy plaintext,
        or ciphertext sealed under a key other than the primary one."""
        if not self.enabled:
            return False
        stored = value.get(JSON_ENVELOPE_KEY) if self.is_encrypted_mapping(value) else value
        if not self.is_encrypted(stored):
            return True
        return self._split(stored)[0] != self.primary_key_id

    # -- text -------------------------------------------------------------------------

    def encrypt(self, plaintext: str, *, context: str = "") -> str:
        if not self._fernets:
            return plaintext
        kid, fernet = self._fernets[0]
        payload = f"{len(context)}:{context}{plaintext}".encode("utf-8")
        return f"{ENVELOPE_PREFIX}{kid}:{fernet.encrypt(payload).decode('ascii')}"

    def decrypt(self, stored: str, *, context: str = "") -> str:
        if not self.is_encrypted(stored):
            return stored  # legacy plaintext row, written before a key was configured
        if not self._fernets:
            raise ConfigurationError(
                f"an encrypted value was read but {self._setting} is not set -- this database was "
                "written by a deployment that had a data key; configure the same key(s)",
                details={"setting": self._setting},
            )
        from cryptography.fernet import InvalidToken

        kid, token = self._split(stored)
        # Named key first; then the rest, so a value whose key id was damaged but whose token is
        # intact still authenticates (the id is a hint, the HMAC is the check).
        ordered = sorted(self._fernets, key=lambda item: item[0] != kid)
        payload: bytes | None = None
        for _, fernet in ordered:
            try:
                payload = fernet.decrypt(token.encode("ascii"))
                break
            except (InvalidToken, ValueError):
                continue
        if payload is None:
            known = kid in self.key_ids
            raise DataIntegrityError(
                "a stored encrypted value failed authentication"
                + ("" if known else " (its key is not among the configured data keys)"),
                details={"reason": "authentication_failed" if known else "unknown_key",
                         "key_id": kid},
            )
        return self._unbind(payload, context=context, kid=kid)

    @staticmethod
    def _split(stored: str) -> tuple[str, str]:
        body = stored[len(ENVELOPE_PREFIX):]
        kid, sep, token = body.partition(":")
        if not sep or not _KID_RE.match(kid) or not token:
            raise DataIntegrityError(
                "a stored encrypted value is malformed", details={"reason": "malformed_envelope"}
            )
        return kid, token

    @staticmethod
    def _unbind(payload: bytes, *, context: str, kid: str) -> str:
        try:
            text = payload.decode("utf-8")
            length_text, sep, rest = text.partition(":")
            length = int(length_text) if sep and length_text.isdigit() else -1
        except (UnicodeDecodeError, ValueError):
            length = -1
        if length < 0 or length > len(rest):
            raise DataIntegrityError(
                "a stored encrypted value has an unreadable payload",
                details={"reason": "malformed_payload", "key_id": kid},
            )
        if rest[:length] != context:
            raise DataIntegrityError(
                "a stored encrypted value belongs to a different row",
                details={"reason": "context_mismatch", "key_id": kid},
            )
        return rest[length:]

    # -- JSON mappings ------------------------------------------------------------------

    def encrypt_mapping(self, mapping: Mapping[str, Any], *, context: str = "") -> dict[str, Any]:
        """Seal a whole JSON object as one value. Passthrough returns a plain copy."""
        if not self._fernets:
            return dict(mapping)
        return {JSON_ENVELOPE_KEY: self.encrypt(json.dumps(dict(mapping)), context=context)}

    def decrypt_mapping(self, stored: Mapping[str, Any] | None, *, context: str = "") -> dict[str, Any]:
        if not stored:
            return {}
        if self.is_encrypted_mapping(stored):
            value = json.loads(self.decrypt(stored[JSON_ENVELOPE_KEY], context=context))
            if not isinstance(value, dict):
                raise DataIntegrityError(
                    "a stored encrypted mapping did not decode to an object",
                    details={"reason": "malformed_payload"},
                )
            return value
        return dict(stored)  # legacy plaintext mapping
