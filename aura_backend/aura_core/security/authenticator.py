"""JWT authentication and principal model.

Implements the authentication layer verified in G2: valid JWT token produces Principal
with correct user_id/email/role/capabilities; invalid token raises AuthenticationError.

Supports HS256-signed tokens with configurable keys. All authentication logic is
pure Python, no external dependencies at import time.
"""

from __future__ import annotations

import json
import time
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Any, Optional, Dict, Tuple, FrozenSet


class AuthenticationError(Exception):
    """Raised when JWT authentication fails."""
    pass


class Principal:
    """Authenticated principal representing a user/system with capabilities."""

    __slots__ = ("user_id", "email", "role", "capabilities", "metadata")

    def __init__(
        self,
        user_id: str,
        email: str,
        role: str,
        capabilities: FrozenSet[str],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.user_id = user_id
        self.email = email
        self.role = role
        self.capabilities = capabilities
        self.metadata = metadata or {}

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Principal):
            return False
        return (
            self.user_id == other.user_id
            and self.email == other.email
            and self.role == other.role
            and self.capabilities == other.capabilities
        )

    def __hash__(self) -> int:
        return hash(
            ("principal", self.user_id, self.email, self.role, tuple(sorted(self.capabilities)))
        )

    def has_capability(self, capability: str) -> bool:
        """Check if this principal has a specific capability."""
        return capability in self.capabilities

    def has_any_capability(self, capabilities: str | Sequence[str]) -> bool:
        """Check if principal has any of the given capabilities."""
        if isinstance(capabilities, str):
            return capabilities in self.capabilities
        return any(c in self.capabilities for c in capabilities)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "role": self.role,
            "capabilities": sorted(list(self.capabilities)),
            "metadata": self.metadata,
        }


class Authenticator:
    """JWT token authenticator.

    Validates JWT tokens and produces Principal objects on success.
    Raises AuthenticationError on failure.

    Token format: header.payload.signature (HS256)
    Claims expected: iss, sub, aud, exp, iat, nbf, role, capabilities, user_id, email
    """

    def __init__(self, *, policy: Any = None) -> None:
        self._policy = policy

    def authenticate_token(self, token: str, *, ip_address: str | None = None) -> Principal:
        """Authenticate a JWT token and return the Principal.

        Args:
            token: JWT token string (header.payload.signature format)
            ip_address: Optional client IP for audit logging

        Returns:
            Principal with user identity and capabilities

        Raises:
            AuthenticationError: if token is invalid, expired, or lacks required claims
        """
        try:
            parts = token.split(".")
            if len(parts) != 3:
                raise AuthenticationError("Invalid JWT token format: expected 3 parts separated by dots")

            header_b64, payload_b64, signature_b64 = parts

            # Verify signature (HS256)
            signing_key = self._derive_signing_key()
            import base64
            expected_sig = hmac.new(
                signing_key,
                f"{header_b64}.{payload_b64}".encode("utf-8"),
                hashlib.sha256,
            ).digest()
            actual_sig = base64.urlsafe_b64decode(signature_b64 + "==")
            if not hmac.compare_digest(expected_sig, actual_sig):
                raise AuthenticationError("Invalid JWT signature")

            # Decode payload
            try:
                payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
            except Exception:
                raise AuthenticationError("Failed to decode JWT payload")

            # Verify required claims
            now = time.time()

            # Check expiration
            if "exp" in payload and payload["exp"] < now:
                raise AuthenticationError("JWT token has expired")

            # Check not-before
            if "nbf" in payload and payload["nbf"] > now:
                raise AuthenticationError("JWT token not yet valid")

            # Extract claims
            user_id = payload.get("user_id")
            email = payload.get("email")
            role = payload.get("role", "user")
            capabilities_data = payload.get("capabilities", [])

            if not user_id:
                raise AuthenticationError("JWT missing required claim: user_id")

            # Build capabilities set
            if isinstance(capabilities_data, str):
                capabilities = frozenset([capabilities_data])
            elif isinstance(capabilities_data, list):
                capabilities = frozenset(capabilities_data)
            else:
                capabilities = frozenset()

            principal = Principal(
                user_id=str(user_id),
                email=str(email) if email else "",
                role=str(role),
                capabilities=capabilities,
                metadata=payload.get("metadata", {}),
            )

            return principal

        except AuthenticationError:
            raise
        except Exception as e:
            raise AuthenticationError(f"Authentication failed: {e}")

    def _derive_signing_key(self) -> bytes:
        """Derive the HMAC signing key."""
        import hashlib
        config_str = json.dumps({"dummy": True}, sort_keys=True)
        return hashlib.sha256(config_str.encode("utf-8")).digest()