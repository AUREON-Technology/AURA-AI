"""The app's ``users`` row for an auth subject, from inside ``aura_core`` (ADR-032).

``UserId`` is the auth subject; the synced tables key on ``users.id`` (a UUID). The v2 layer
creates the row on first sight (``infrastructure/db/user_repo.get_or_create_user``); the core may
not import that (ADR-002), so this is the same rule in SQL: find by ``auth_subject``, or insert
with the same placeholder email ``get_or_create_user`` uses when the token carries none. The
``role`` is the column default -- never written here, for the reason ``get_or_create_user``
gives.
"""

from __future__ import annotations

import uuid
from typing import Any

__all__ = ["find_user_id", "ensure_user_id"]


async def find_user_id(conn: Any, subject: str) -> uuid.UUID | None:
    return await conn.fetchval("SELECT id FROM users WHERE auth_subject = $1", subject)


async def ensure_user_id(conn: Any, subject: str) -> uuid.UUID:
    existing = await find_user_id(conn, subject)
    if existing is not None:
        return existing
    await conn.execute(
        """
        INSERT INTO users (id, auth_subject, email, display_name)
        VALUES ($1, $2, $3, '')
        ON CONFLICT DO NOTHING
        """,
        uuid.uuid4(), subject, f"{subject}@unknown.local",
    )
    created = await find_user_id(conn, subject)
    if created is None:  # pragma: no cover - only if the email placeholder collided
        raise RuntimeError("could not create the users row for this subject")
    return created
