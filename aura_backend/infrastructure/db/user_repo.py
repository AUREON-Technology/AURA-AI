"""Shared user lookup/creation, used by every v2 router that needs the
internal Postgres user row for the caller's verified auth subject (see
infrastructure/db/models.py's User.auth_subject doc comment). Factored out
of api/v2/sync.py so api/v2/insights.py doesn't duplicate it.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User

logger = logging.getLogger("aura_backend.user_repo")

#: The stand-in email of a row created before any token with an email was seen
#: (``aura_core/adapters/postgres_app_users.ensure_user_id``, and below when a token has none).
PLACEHOLDER_EMAIL_SUFFIX = "@unknown.local"


def is_placeholder_email(email: str | None) -> bool:
    return bool(email) and email.lower().endswith(PLACEHOLDER_EMAIL_SUFFIX)


async def get_or_create_user(db: AsyncSession, user_info: dict) -> User:
    """The caller's row, created on first sight.

    Never writes ``role``. That column is the Access page's: a sign-in used to copy the token's
    resolved role into it (mostly an env-list role) and only ever upward, so the column ended up
    holding roles nobody assigned and kept them after the source was removed. Role resolution
    now reads the column when an operator assigned it (``role_assigned_at``), which only works if
    sign-in stops writing it.

    A placeholder email is replaced by the token's own when that one is verified (see
    ``_replace_placeholder_email``). Any other email on an existing row is left alone.
    """
    subject = user_info["uid"]
    result = await db.execute(select(User).where(User.auth_subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            auth_subject=subject,
            email=user_info.get("email") or f"{subject}{PLACEHOLDER_EMAIL_SUFFIX}",
            display_name=user_info.get("name") or "",
            photo_url=user_info.get("picture"),
            role="User",
        )
        db.add(user)
        await db.flush()
    elif is_placeholder_email(user.email):
        await _replace_placeholder_email(db, user, user_info)
    return user


async def _replace_placeholder_email(db: AsyncSession, user: User, user_info: dict) -> None:
    """Give a ``…@unknown.local`` row the person's real address.

    The Access page finds people by exact email, so a row left on its placeholder can never be
    given a role, however many times its owner signs in with a real address afterwards.

    Only a verified email counts: an unverified one is whatever the person typed. Only another
    placeholder is replaced, never a real address. And never an address another account holds --
    the column is unique, and taking one over would be a takeover, so that sign-in keeps its
    placeholder and is left for an operator to resolve.
    """
    email = (user_info.get("email") or "").strip()
    if not email or is_placeholder_email(email) or user_info.get("email_verified") is not True:
        return
    taken = await db.execute(
        select(User).where(func.lower(User.email) == email.lower(), User.id != user.id)
    )
    if taken.scalar_one_or_none() is not None:
        logger.warning("kept a placeholder email: the verified address belongs to another account")
        return
    user.email = email
    await db.flush()


class RoleLookupUnavailable(RuntimeError):
    """The Access-page assignment could not be read -- the database failed -- as opposed to "no
    role assigned". Raised only inside ``role_lookup_must_succeed``."""


_strict_role_lookup: ContextVar[bool] = ContextVar("aura_strict_role_lookup", default=False)


@contextmanager
def role_lookup_must_succeed() -> Iterator[None]:
    """Within this block ``assigned_role`` raises ``RoleLookupUnavailable`` on a database error
    instead of reading it as "none assigned".

    For a caller whose *refusal* depends on the assignment: a desktop refresh ends the session of
    anyone below the console role, and has already spent the refresh token, so an outage read as
    "no assignment" would demote an operator and sign them out (INT-104). Opt-in, so every other
    caller (``services.rbac.stamp_role`` at sign-in) keeps the lenient reading. A context
    variable rather than a parameter because the read sits behind ``stamp_role``."""
    token = _strict_role_lookup.set(True)
    try:
        yield
    finally:
        _strict_role_lookup.reset(token)


async def assigned_role(subject: str) -> str | None:
    """The role an operator assigned this subject on the Access page, or None.

    None also when Postgres is not configured or cannot be reached: an assignment that cannot be
    read is not granted, so an outage can only lower a role to what the env lists and roles.json
    give, never raise one. Inside ``role_lookup_must_succeed`` an unreachable database raises
    ``RoleLookupUnavailable`` instead.
    """
    from .base import get_session_factory, is_configured

    if not subject or not is_configured():
        return None
    try:
        async with get_session_factory()() as db:
            return await db.scalar(
                select(User.role).where(
                    User.auth_subject == subject, User.role_assigned_at.is_not(None)
                )
            )
    except Exception as exc:
        if _strict_role_lookup.get():
            logger.warning("could not read the assigned role", exc_info=True)
            raise RoleLookupUnavailable("the role assignment could not be read") from exc
        logger.exception("could not read the assigned role; resolving without it")
        return None
