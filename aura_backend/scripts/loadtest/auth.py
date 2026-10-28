"""Per-virtual-user identities.

Three modes, none of which puts a secret in the repository or the results file:

- ``none``: no ``Authorization`` header. Only for a gateway running with mock auth
  (``AURA_ALLOW_MOCK_AUTH``, refused by settings validation in staging/production), where every
  caller becomes the same ``mock-user`` -- per-user limits and per-user state then collapse
  into one user, which the report states.
- ``tokens``: access tokens obtained through the real login flow (``/api/v3/auth/token`` or
  ``/web/token``), one per line in a file, assigned to VUs round-robin. Access tokens live
  ``ACCESS_TOKEN_TTL`` (15 minutes), so this suits short runs only.
- ``mint``: the documented operator path for a full matrix. The operator of the target already
  holds its JWT signing secret (``deploy/secrets/jwt_secret``); the harness reads it from an
  environment variable whose *name* is configured (default ``AURA_LOADTEST_JWT_SECRET``) and
  mints one short-lived HS256 access token per VU with exactly the claim shape
  ``services/jwt_auth.create_access_token`` issues (``iss``/``aud`` included), for synthetic
  users ``<prefix>-001`` ... It
  grants nothing an operator with that secret could not already do, and it is refused for the
  admin roles so a load run can never exercise admin-only paths by accident.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field

#: Roles a minted load-test identity may carry. ``Developer`` is there because the Engineer
#: scenario needs a role listed in the target's ``AURA_ENGINEER_AGENT_ROLES``.
MINTABLE_ROLES = ("User", "Premium", "Developer")

#: Re-mint when a token has less than this left, so a long matrix never runs into expiry.
_REMINT_MARGIN_S = 120

#: The ``iss``/``aud`` a production gateway requires (``services/jwt_auth._issuer``/``_audience``
#: with ``AURA_ENVIRONMENT=production``). ``minted_from_env`` uses these unless
#: ``AURA_LOADTEST_JWT_ISSUER``/``AURA_LOADTEST_JWT_AUDIENCE`` name another target's values.
DEFAULT_TARGET_ISSUER = "aura-production"
DEFAULT_TARGET_AUDIENCE = "aura-api-production"


class AuthError(RuntimeError):
    pass


@dataclass
class TokenSource:
    mode: str
    user_prefix: str = "loadtest-vu"
    _static: list[str] = field(default_factory=list)
    _secret: str | None = field(default=None, repr=False)
    role: str = "User"
    ttl_s: int = 3600
    issuer: str | None = None
    audience: str | None = None
    _minted: dict[int, tuple[str, float]] = field(default_factory=dict, repr=False)

    # -- construction ------------------------------------------------------------------

    @classmethod
    def none(cls) -> "TokenSource":
        return cls(mode="none")

    @classmethod
    def from_file(cls, path: str) -> "TokenSource":
        with open(path, encoding="utf-8") as handle:
            tokens = [line.strip() for line in handle if line.strip() and not line.startswith("#")]
        if not tokens:
            raise AuthError(f"tokens file {path} contains no tokens")
        return cls(mode="tokens", _static=tokens)

    @classmethod
    def minted(
        cls, *, secret: str, role: str = "User", user_prefix: str = "loadtest-vu", ttl_s: int = 3600,
        issuer: str | None = None, audience: str | None = None,
    ) -> "TokenSource":
        if not secret:
            raise AuthError("mint mode needs a non-empty signing secret")
        if role not in MINTABLE_ROLES:
            raise AuthError(
                f"refusing to mint role {role!r}: load-test identities are limited to "
                f"{', '.join(MINTABLE_ROLES)}"
            )
        return cls(
            mode="mint", _secret=secret, role=role, user_prefix=user_prefix, ttl_s=ttl_s,
            issuer=issuer, audience=audience,
        )

    @classmethod
    def minted_from_env(cls, env_name: str, **kwargs) -> "TokenSource":
        secret = os.environ.get(env_name, "")
        if not secret:
            raise AuthError(
                f"mint mode reads the target's JWT secret from ${env_name}, which is not set "
                "(see docs/guides/load-testing.md, 'Identities')"
            )
        kwargs.setdefault("issuer", os.environ.get("AURA_LOADTEST_JWT_ISSUER") or DEFAULT_TARGET_ISSUER)
        kwargs.setdefault(
            "audience", os.environ.get("AURA_LOADTEST_JWT_AUDIENCE") or DEFAULT_TARGET_AUDIENCE,
        )
        return cls.minted(secret=secret, **kwargs)

    # -- use ---------------------------------------------------------------------------

    @property
    def distinct_users(self) -> int | None:
        """How many distinct identities exist; ``None`` means one per VU (minted)."""
        if self.mode == "tokens":
            return len(self._static)
        if self.mode == "none":
            return 1
        return None

    def user_id(self, vu: int) -> str:
        if self.mode == "mint":
            return f"{self.user_prefix}-{vu + 1:03d}"
        if self.mode == "tokens":
            return f"token-{vu % len(self._static) + 1}"
        return "mock-user"

    def token(self, vu: int) -> str | None:
        if self.mode == "none":
            return None
        if self.mode == "tokens":
            return self._static[vu % len(self._static)]
        cached = self._minted.get(vu)
        now = time.time()
        if cached is None or cached[1] - now < _REMINT_MARGIN_S:
            cached = (self._mint(vu, now), now + self.ttl_s)
            self._minted[vu] = cached
        return cached[0]

    def headers(self, vu: int) -> dict[str, str]:
        token = self.token(vu)
        return {"Authorization": f"Bearer {token}"} if token else {}

    def _mint(self, vu: int, now: float) -> str:
        try:
            import jwt  # PyJWT, already a runtime dependency of the backend
        except ImportError as exc:  # pragma: no cover - PyJWT is in requirements.lock
            raise AuthError("mint mode needs PyJWT installed (pip install pyjwt)") from exc
        uid = self.user_id(vu)
        payload = {
            "sub": uid,
            "email": f"{uid}@loadtest.invalid",
            "name": f"Load test {uid}",
            "picture": None,
            "role": self.role,
            "iat": int(now),
            "exp": int(now + self.ttl_s),
        }
        # The gateway refuses a token bound to another deployment, and accepts an unbound one
        # only during the legacy grace period (services/jwt_auth._decode_access_token).
        if self.issuer:
            payload["iss"] = self.issuer
        if self.audience:
            payload["aud"] = self.audience
        return jwt.encode(payload, self._secret, algorithm="HS256")

    def describe(self) -> str:
        """For the report: the mode and shape, never a token or the secret."""
        if self.mode == "mint":
            return (
                f"mint (HS256, role={self.role}, users {self.user_prefix}-NNN, "
                f"ttl {self.ttl_s}s, one identity per VU)"
            )
        if self.mode == "tokens":
            return f"tokens ({len(self._static)} distinct identities, round-robin over VUs)"
        return "none (mock auth: every VU is the same user)"
