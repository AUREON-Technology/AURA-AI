"""The signed-in user's own profile: ``GET /api/v3/me``, ``PATCH /api/v3/me``, ``POST /api/v3/me/password``.

Sign-in, and the password behind it, live at Firebase (ADR-002's "permanent platform adapters"
sit outside ``aura_core``; the identity provider is not ours to replace here). This server stores
no password and no hash. So three things a person expects from "my account" were missing:

* ``GET /me`` -- who am I, as this server knows me. Until now only operators had one
  (``/admin/me``).
* ``PATCH /me`` -- the fields a person owns: display name, photo, language, time zone. The email
  is *not* among them: role grants and the Access page key on it (services/rbac.py), and the
  verified one comes from the identity provider, so a free-text edit would be an account-takeover
  primitive. ``extra="forbid"`` makes an attempt a 422 rather than a silent no-op.
* ``POST /me/password`` -- change the Firebase password. The Admin SDK can set a password without
  knowing the old one, so a stolen access token alone must not be enough: the call needs a
  *fresh Firebase ID token from signing in with the current password* (``sign_in_provider ==
  "password"``, ``auth_time`` within ``PASSWORD_REAUTH_MAX_AGE_S``), for the same account. That is
  the proof of the current password; the server never sees the password itself, only the new one.
  An account that signs in only with Google has no password to change and is told so (409).

Register, forgot-password and reset-password stay in the client's Firebase SDK on purpose: they
run before any access token exists, so there is no caller for this server to authenticate.

After a password change every session and paired device of the account is revoked here and the
Firebase refresh tokens are revoked there. An access token already issued stays valid until its
own expiry (minutes); that is the same window ``/auth/revoke`` has.

Nothing here logs a password, a token, a subject or an email.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urlparse

import anyio
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.v2.auth import _sliding_limit
from infrastructure.db.base import get_db, is_configured
from infrastructure.db.user_repo import get_or_create_user, is_placeholder_email
from aura_core.security.policy import SecurityPolicy
from services.jwt_auth import (
    AuthStateUnavailable,
    revoke_all_devices_for_uid,
    revoke_all_for_uid,
    verify_access_token,
)
from services.user_language import normalize_language, resolve_language
from services.user_time import normalize_timezone

logger = logging.getLogger("aura.me")

router = APIRouter(prefix="/api/v3/me", tags=["profile"])

MAX_DISPLAY_NAME = 200  # users.display_name is VARCHAR(200)
MAX_PHOTO_URL = 1000  # users.photo_url is VARCHAR(1000)
#: The fresh sign-in a password change needs must be this recent.
PASSWORD_REAUTH_MAX_AGE_S = 300
#: A token claiming to be issued further in the future than this is not trusted (clock skew).
_FUTURE_SKEW_S = 120
#: Password changes per account in ``_PASSWORD_WINDOW_S``. The route is behind a fresh sign-in
#: already; this bounds a stolen-session attacker's retries and the Firebase calls we spend.
PASSWORD_CHANGES_PER_WINDOW = 5
_PASSWORD_WINDOW_S = 600.0
MAX_PASSWORD_LENGTH = 256
_password_hits: dict = {}

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_SYMBOL = re.compile(r"[^A-Za-z0-9]")


def _reset_me_rate_limits() -> None:
    _password_hits.clear()


def _require_db_configured() -> None:
    if not is_configured():
        raise HTTPException(status_code=503, detail="Postgres is not configured (DATABASE_URL unset).")


# --- models ---

class ProfileResponse(BaseModel):
    id: str
    #: ``None`` while the account only has the placeholder address an identity token without an
    #: email gets (``…@unknown.local``): that is not an address anyone owns.
    email: str | None
    email_verified: bool
    display_name: str
    photo_url: str | None
    #: ``"ar"`` or ``"en"``; English for an account that never reported one.
    language: str
    #: IANA zone name, or ``None`` (read as UTC) when the app never reported one.
    timezone: str | None
    role: str
    created_at: str


class ProfileUpdate(BaseModel):
    """Only the fields a person owns. Anything else, the email included, is a 422."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = Field(default=None, description="Empty clears it. Never null.")
    photo_url: str | None = Field(default=None, description="https URL, or null to remove.")
    language: str | None = Field(default=None, description="'ar' or 'en' (a BCP-47 tag is reduced).")
    timezone: str | None = Field(default=None, description="An IANA zone name such as 'Africa/Cairo'.")


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_password: str = Field(max_length=MAX_PASSWORD_LENGTH)
    #: A Firebase ID token obtained by signing in with the CURRENT email and password just now.
    id_token: str = Field(min_length=1, max_length=8192)


# --- helpers ---

def _profile(user: Any, claims: dict) -> ProfileResponse:
    placeholder = is_placeholder_email(user.email)
    return ProfileResponse(
        id=str(user.id),
        email=None if placeholder else user.email,
        email_verified=bool(claims.get("email_verified")) and not placeholder,
        display_name=user.display_name or "",
        photo_url=user.photo_url,
        language=resolve_language(user.language),
        timezone=user.timezone,
        role=str(claims.get("role") or user.role or "User"),
        created_at=user.created_at.isoformat() if user.created_at else "",
    )


def _unprocessable(field: str, message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "invalid_field", "field": field, "detail": message})


def _clean_display_name(value: str) -> str:
    name = value.strip()
    if len(name) > MAX_DISPLAY_NAME:
        raise _unprocessable("display_name", f"At most {MAX_DISPLAY_NAME} characters.")
    if _CONTROL.search(name):
        raise _unprocessable("display_name", "Control characters are not allowed.")
    return name


def _clean_photo_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if len(url) > MAX_PHOTO_URL or parsed.scheme != "https" or not parsed.netloc or _CONTROL.search(url):
        raise _unprocessable("photo_url", f"An https URL of at most {MAX_PHOTO_URL} characters, or null.")
    return url


def password_problems(password: str, policy: SecurityPolicy | None = None) -> list[str]:
    """What a new password lacks under the platform's own policy (``SecurityPolicy``).

    Names the requirements, never echoes the password. Empty means acceptable.
    """
    policy = policy or SecurityPolicy()
    problems: list[str] = []
    if len(password) < policy.password_min_length:
        problems.append(f"at least {policy.password_min_length} characters")
    if policy.password_require_uppercase and not any(c.isupper() for c in password):
        problems.append("an uppercase letter")
    if policy.password_require_lowercase and not any(c.islower() for c in password):
        problems.append("a lowercase letter")
    if policy.password_require_numbers and not any(c.isdigit() for c in password):
        problems.append("a digit")
    if policy.password_require_symbols and not _SYMBOL.search(password):
        problems.append("a symbol")
    return problems


def _firebase_available() -> bool:
    from services import security

    return security.firebase_app is not None


async def _verify_fresh_identity(id_token: str) -> dict:
    """Verify ``id_token`` as a live (not revoked) Firebase ID token and return its claims
    plus the subject this server would sign it in as. Raises ``ValueError`` for a token that
    does not verify. The seam tests replace; it is the only place this route reads Firebase."""
    from firebase_admin import auth

    from services.security import canonical_subject

    try:
        decoded = await anyio.to_thread.run_sync(lambda: auth.verify_id_token(id_token, check_revoked=True))
    except Exception as exc:  # expired, malformed, wrong project, revoked, network
        raise ValueError("the sign-in could not be verified") from exc
    return {**decoded, "_canonical_subject": await canonical_subject(decoded)}


async def _set_firebase_password(firebase_uid: str, new_password: str) -> None:
    """Set the password, then revoke the account's Firebase refresh tokens so no other device
    keeps minting ID tokens from the old credential. Seam for tests."""
    from firebase_admin import auth

    def _apply() -> None:
        auth.update_user(firebase_uid, password=new_password)
        auth.revoke_refresh_tokens(firebase_uid)

    await anyio.to_thread.run_sync(_apply)


# --- routes ---

@router.get("", response_model=ProfileResponse)
async def get_profile(
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """The caller's own profile, created on first sight like every other signed-in route."""
    _require_db_configured()
    user = await get_or_create_user(db, user_info)
    await db.commit()
    return _profile(user, user_info)


@router.patch("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    user_info: dict = Depends(verify_access_token),
    db: AsyncSession = Depends(get_db),
) -> ProfileResponse:
    """Change the fields the caller owns. Send only what changes; at least one field."""
    _require_db_configured()
    sent = body.model_fields_set
    if not sent:
        raise HTTPException(status_code=422, detail={"error": "empty_update", "detail": "Send at least one field."})

    changes: dict[str, Any] = {}
    if "display_name" in sent:
        if body.display_name is None:
            raise _unprocessable("display_name", "Cannot be null; send an empty string to clear it.")
        changes["display_name"] = _clean_display_name(body.display_name)
    if "photo_url" in sent:
        changes["photo_url"] = None if body.photo_url is None else _clean_photo_url(body.photo_url)
    if "language" in sent:
        language = normalize_language(body.language)
        if language is None:
            raise _unprocessable("language", "Supported languages: ar, en.")
        changes["language"] = language
    if "timezone" in sent:
        zone = normalize_timezone(body.timezone)
        if zone is None:
            raise _unprocessable("timezone", "Not an IANA zone name (for example Africa/Cairo).")
        changes["timezone"] = zone

    user = await get_or_create_user(db, user_info)
    for name, value in changes.items():
        setattr(user, name, value)
    await db.commit()
    logger.info("profile updated", extra={"fields": sorted(changes)})
    return _profile(user, user_info)


@router.post("/password")
async def change_password(
    body: PasswordChange,
    user_info: dict = Depends(verify_access_token),
) -> dict:
    """Change the Firebase password. Needs a fresh sign-in with the current one (see module doc).

    Never reads or stores the old password. Revokes every session and paired device of the
    account on success; the client signs the person out and back in.
    """
    subject = user_info.get("uid")
    if not subject:
        raise HTTPException(status_code=401, detail="This token does not name an account.")
    # Before any work: a flood of attempts costs a counter, not a Firebase call.
    _sliding_limit(_password_hits, str(subject), PASSWORD_CHANGES_PER_WINDOW, _PASSWORD_WINDOW_S,
                   "Too many password changes; try again later.")

    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(status_code=422, detail={
            "error": "weak_password",
            "detail": "The new password needs: " + ", ".join(problems) + ".",
            "requirements": problems,
        })
    if not _firebase_available():
        raise HTTPException(status_code=503, detail={
            "error": "password_change_unavailable",
            "detail": "Password changes are not available on this server.",
        })

    try:
        claims = await _verify_fresh_identity(body.id_token)
    except ValueError:
        raise HTTPException(status_code=401, detail={
            "error": "reauth_required",
            "detail": "Sign in again with your current password, then retry.",
        }) from None
    if claims.get("_canonical_subject") != subject:
        # Someone else's token. Same message as a stale one: no hint which account it was.
        raise HTTPException(status_code=401, detail={
            "error": "reauth_required",
            "detail": "Sign in again with your current password, then retry.",
        })
    provider = (claims.get("firebase") or {}).get("sign_in_provider")
    if provider != "password":
        raise HTTPException(status_code=409, detail={
            "error": "password_not_enabled",
            "detail": "This account does not sign in with a password, so there is none to change. "
                      "Sign in with your email and password to change it.",
        })
    auth_time = claims.get("auth_time")
    now = time.time()
    if (not isinstance(auth_time, (int, float)) or isinstance(auth_time, bool)
            or now - auth_time > PASSWORD_REAUTH_MAX_AGE_S or auth_time - now > _FUTURE_SKEW_S):
        raise HTTPException(status_code=401, detail={
            "error": "reauth_required",
            "detail": "Sign in again with your current password, then retry.",
        })
    firebase_uid = claims.get("uid") or claims.get("user_id") or claims.get("sub")
    if not firebase_uid:
        raise HTTPException(status_code=401, detail={"error": "reauth_required", "detail": "Sign in again."})

    try:
        await _set_firebase_password(str(firebase_uid), body.new_password)
    except ValueError:
        # The Admin SDK rejects a password it considers invalid (length) before calling Firebase.
        raise HTTPException(status_code=422, detail={"error": "weak_password", "detail": "Password rejected."}) from None
    except Exception:
        logger.error("password change failed at the identity provider", exc_info=True)
        raise HTTPException(status_code=502, detail={
            "error": "identity_provider_error",
            "detail": "The password was not changed. Try again in a minute.",
        }) from None

    result: dict[str, Any] = {"changed": True, "sign_in_again": True}
    try:
        result["sessions_revoked"] = revoke_all_for_uid(str(subject))
        result["devices_revoked"] = revoke_all_devices_for_uid(str(subject))
    except AuthStateUnavailable:
        # The password IS changed. Say the revocation did not complete rather than failing a
        # change that happened; the client still signs out, and the operator route can finish it.
        logger.warning("password changed but session revocation could not be completed")
        result["sessions_revoked"] = None
        result["devices_revoked"] = None
        result["revocation_incomplete"] = True
    logger.info("password changed")
    return result
