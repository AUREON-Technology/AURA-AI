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
        )
        db.add(user)
        await db.flush()
    return user
