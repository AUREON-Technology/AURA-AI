"""Shared user lookup/creation, used by every v2 router that needs the
internal Postgres user row for the caller's verified auth subject (see
infrastructure/db/models.py's User.auth_subject doc comment). Factored out
of api/v2/sync.py so api/v2/insights.py doesn't duplicate it.
"""
import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User

logger = logging.getLogger("aura_backend.user_repo")


async def get_or_create_user(db: AsyncSession, user_info: dict) -> User:
    """The caller's row, created on first sight.

    Never writes ``role``. That column is the Access page's: a sign-in used to copy the token's
    resolved role into it (mostly an env-list role) and only ever upward, so the column ended up
    holding roles nobody assigned and kept them after the source was removed. Role resolution
    now reads the column when an operator assigned it (``role_assigned_at``), which only works if
    sign-in stops writing it.
    """
    subject = user_info["uid"]
    result = await db.execute(select(User).where(User.auth_subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            auth_subject=subject,
            email=user_info.get("email") or f"{subject}@unknown.local",
            display_name=user_info.get("name") or "",
            photo_url=user_info.get("picture"),
            role="User",
        )
        db.add(user)
        await db.flush()
    return user


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
