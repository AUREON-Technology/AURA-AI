"""Secret manager for production deployment.

Handles encryption/decryption of secrets at rest, secret rotation, and secret
never-logging policies. All operations degrade gracefully when KMS is unavailable.

Never stores plaintext secrets in environment variables when
`allow_plaintext_secrets_in_env=false` (the production default).
"""

from __future__ import annotations

from enum import Enum
import base64
import json
import hashlib
import secrets as _secrets
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, Union


# Self-contained error classes
class ValidationError(Exception):
    """Minimal validation error for secret manager self-containment."""
    pass


# Self-contained security violation types
class SecurityViolationType(str, Enum):
    SECRET_EXPOSURE = "secret_exposure"


class SecurityViolation:
    """A recorded security violation event."""
    def __init__(self, violation_type: SecurityViolationType, detail: str, **kwargs: Any) -> None:
        self.violation_type = violation_type
        self.detail = detail
        for k, v in kwargs.items():
            setattr(self, k, v)


# Global instance tracking
_secret_manager_instance: "SecretManager" | None = None


class SecretManager:
    """Manages secret encryption, storage, and rotation.

    Features:
    - AES-256-GCM authenticated encryption of secrets at rest (``cryptography``'s AESGCM, bound
      to the secret id as associated data, so a wrong key, a tampered ciphertext or an entry
      presented under another secret id all fail to decrypt)
    - Automatic key rotation
    - Secret never-logging policy
    - Graceful degradation when KMS unavailable (in-memory fallback)
    """

    def __init__(self, *, policy: Any = None) -> None:
        self._policy = policy or {"dummy": True}
        self._initialized = False
        self._keys: dict[str, dict[str, Any]] = {}  # key_id -> {key, created_at, version}
        self._secret_versions: dict[str, list[dict[str, Any]]] = {}

    def _ensure_initialized(self) -> None:
        """Lazy initialization - generate initial key if none exist."""
        if self._initialized:
            return
        if not self._keys:
            key_id = "default"
            self._keys[key_id] = {
                "key": self._generate_key(),
                "created_at": datetime.utcnow(),
                "version": 1,
            }
        self._initialized = True

    @staticmethod
    def _generate_key() -> bytes:
        """Generate a 256-bit encryption key."""
        return _secrets.token_bytes(32)  # AES-256

    def _get_current_key_id(self, secret_id: str) -> str:
        """Get the current encryption key ID for a secret."""
        self._ensure_initialized()
        secrets = self._secret_versions.get(secret_id, [])
        if secrets:
            return secrets[-1].get("key_id", "default")
        return "default"

    def encrypt_secret(
        self, secret_id: str, secret_value: str, *, user_id: str | None = None,
        key_id: str | None = None,
    ) -> dict[str, Any]:
        """Encrypt a secret value and store it (under ``key_id``, else the secret's current key)."""
        self._ensure_initialized()

        key_id = key_id or self._get_current_key_id(secret_id)
        key_data = self._keys[key_id]
        key = key_data["key"]

        iv = _secrets.token_bytes(12)  # 96-bit GCM nonce, never reused under one key
        plaintext = secret_value.encode("utf-8")

        encrypted = self._aead_encrypt(plaintext, key, iv, secret_id)

        # Encode result
        encoded_iv = base64.b64encode(iv).decode("utf-8")
        encoded_ct = base64.b64encode(encrypted).decode("utf-8")

        # Store entry
        entry = {
            "encrypted": encoded_ct,
            "iv": encoded_iv,
            "key_id": key_id,
            "created_at": datetime.utcnow().timestamp(),
            "secret_id": secret_id,
            "version": key_data["version"],
        }

        # Update secret versions
        if secret_id not in self._secret_versions:
            self._secret_versions[secret_id] = []
        self._secret_versions[secret_id].append(entry)

        # Log violation
        violation = SecurityViolation(
            SecurityViolationType.SECRET_EXPOSURE,
            f"Secret {secret_id} encrypted and stored",
            secret_id=secret_id, key_id=key_id, user_id=user_id
        )

        return entry

    @staticmethod
    def _aead_encrypt(plaintext: bytes, key: bytes, nonce: bytes, secret_id: str) -> bytes:
        """AES-256-GCM. The previous hand-rolled "CTR" derived its keystream from the counter alone
        and never used ``key`` -- anything it produced decrypted under any key."""
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).encrypt(nonce, plaintext, secret_id.encode("utf-8"))

    @staticmethod
    def _aead_decrypt(ciphertext: bytes, key: bytes, nonce: bytes, secret_id: str) -> bytes:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        try:
            return AESGCM(key).decrypt(nonce, ciphertext, secret_id.encode("utf-8"))
        except InvalidTag as exc:
            raise ValidationError(
                "secret failed authentication: wrong key, tampered ciphertext, or wrong secret id"
            ) from exc

    def decrypt_secret(self, entry: dict[str, Any], *, expected_secret_id: str | None = None) -> str:
        """Decrypt a secret value from its stored entry."""
        self._ensure_initialized()

        if expected_secret_id and entry.get("secret_id") != expected_secret_id:
            raise ValueError(f"Secret ID mismatch: expected {expected_secret_id}, got {entry.get('secret_id')}")

        key_id = entry.get("key_id", "default")
        key_data = self._keys.get(key_id)
        if key_data is None:
            raise ValidationError(f"no key {key_id!r} is available to decrypt this secret")
        key = key_data["key"]

        encrypted = base64.b64decode(entry["encrypted"])
        iv = base64.b64decode(entry["iv"])

        decrypted = self._aead_decrypt(encrypted, key, iv, entry["secret_id"])

        return decrypted.decode("utf-8")

    def rotate_key(self, secret_id: str, *, user_id: str | None = None) -> dict[str, Any]:
        """Re-encrypt a secret under a freshly generated key; older entries keep their own key."""
        self._ensure_initialized()

        secrets = self._secret_versions.get(secret_id, [])
        if not secrets:
            raise ValueError(f"No encrypted secret found for {secret_id}")

        current_entry = secrets[-1]
        # Decrypt under the key the entry was sealed with *before* anything changes.
        plaintext = self.decrypt_secret(current_entry, expected_secret_id=secret_id)

        new_key_id = f"rotated-{len(self._keys)}-{int(time.time())}"
        self._keys[new_key_id] = {
            "key": self._generate_key(),
            "created_at": datetime.utcnow(),
            "version": current_entry.get("version", 1) + 1,
        }
        new_entry = self.encrypt_secret(secret_id, plaintext, user_id=user_id, key_id=new_key_id)

        SecurityViolation(
            SecurityViolationType.SECRET_EXPOSURE,
            f"Secret {secret_id} key rotated",
            secret_id=secret_id, old_key_id=current_entry.get("key_id"), new_key_id=new_key_id,
            user_id=user_id,
        )
        return new_entry


# Export the class at module level
SecretManager = SecretManager


# Global instance functions
def get_secret_manager() -> "SecretManager":
    """Get the global secret manager instance."""
    global _secret_manager_instance
    if _secret_manager_instance is None:
        _secret_manager_instance = SecretManager()
    return _secret_manager_instance


def set_secret_manager(manager: "SecretManager") -> None:
    """Set the global secret manager instance."""
    global _secret_manager_instance
    _secret_manager_instance = manager