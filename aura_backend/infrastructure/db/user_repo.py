"""Shared user lookup/creation, used by every v2 router that needs the
internal Postgres user row for the caller's verified auth subject (see
infrastructure/db/models.py's User.auth_subject doc comment). Factored out
of api/v2/sync.py so api/v2/insights.py doesn't duplicate it.
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User


async def get_or_create_user(db: AsyncSession, user_info: dict) -> User:
    subject = user_info["uid"]
    result = await db.execute(select(User).where(User.auth_subject == subject))
    user = result.scalar_one_or_none()
    if user is None:
        user = User(
            auth_subject=subject,
            email=user_info.get("email") or f"{subject}@unknown.local",
            display_name=user_info.get("name") or "",
            photo_url=user_info.get("picture"),
            role=user_info.get("role") or "User",
        )
        db.add(user)
        await db.flush()
    elif user_info.get("role") and user.role != user_info["role"]:
        # Persist Access-page assignments and first-login env resolution.
        # Never overwrite SuperAdmin with a lower env-derived role here —
        # callers must already have resolved the highest applicable role.
        from services.rbac import ROLE_HIERARCHY

        incoming = user_info["role"]
        if ROLE_HIERARCHY.get(incoming, 0) >= ROLE_HIERARCHY.get(user.role, 0):
            user.role = incoming
    return user
