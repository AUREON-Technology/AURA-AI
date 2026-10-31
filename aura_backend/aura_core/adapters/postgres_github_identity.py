"""Postgres implementation of ``GitHubIdentityPort`` -- the per-user GitHub OAuth connect flow.

Reads/writes the ``github_identities`` table Alembic migration ``0005_github_identities``
creates (``infrastructure/db/models.py``'s ``GitHubIdentity``) the same way ``postgres_goals.py``
reads ``goals`` and ``postgres_insights.py`` writes ``insights``: raw ``asyncpg`` SQL against a
table owned by that migration, never a ``CREATE TABLE`` of its own, joined through
``users.auth_subject`` because :class:`~aura_core.domain.identifiers.UserId` is the auth-subject
claim, not the table's own UUID primary key.

Unlike those two ports, this one decrypts on read and encrypts on write (``secrets_crypto``) --
the one place in the codebase a third-party credential is both stored in and read back out of
Postgres in the clear, rather than living only in an env var.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from ..domain.errors import DependencyUnavailableError
from ..domain.github_identity import GitHubIdentity
from ..domain.identifiers import UserId
from .postgres_store import PostgresPool
from .secrets_crypto import decrypt_secret, encrypt_secret

logger = logging.getLogger(__name__)

__all__ = ["PostgresGitHubIdentityPort"]


def _unavailable(message: str, **details) -> DependencyUnavailableError:
    return DependencyUnavailableError(message, dependency="postgres", details=details or None)


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


class PostgresGitHubIdentityPort:
    def __init__(self, url: str, *, pool: PostgresPool | None = None) -> None:
        self._pool = pool or PostgresPool(url)

    async def get(self, user_id: UserId) -> GitHubIdentity | None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    """
                    SELECT g.github_user_id, g.github_login, g.access_token_enc,
                           g.refresh_token_enc, g.token_expires_at, g.refresh_token_expires_at,
                           g.installation_id, g.scopes, g.created_at, g.updated_at
                    FROM github_identities g
                    JOIN users u ON u.id = g.user_id
                    WHERE u.auth_subject = $1
                    """,
                    user_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not read GitHub identity: {exc}") from exc
        if row is None:
            return None
        return GitHubIdentity(
            github_user_id=row["github_user_id"],
            github_login=row["github_login"],
            access_token=decrypt_secret(row["access_token_enc"]),
            refresh_token=(
                decrypt_secret(row["refresh_token_enc"]) if row["refresh_token_enc"] else None
            ),
            token_expires_at=_utc(row["token_expires_at"]),
            refresh_token_expires_at=_utc(row["refresh_token_expires_at"]),
            installation_id=row["installation_id"],
            scopes=row["scopes"] or "",
            connected_at=_utc(row["created_at"]),
            updated_at=_utc(row["updated_at"]),
        )

    async def upsert(self, user_id: UserId, identity: GitHubIdentity) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                # SELECT ... FROM users silently inserts zero rows for an unknown auth_subject
                # rather than raising -- matches postgres_insights.py's reasoning; a token cannot
                # be attached to a user row that does not exist.
                await conn.execute(
                    """
                    INSERT INTO github_identities (
                        id, user_id, github_user_id, github_login, access_token_enc,
                        refresh_token_enc, token_expires_at, refresh_token_expires_at,
                        installation_id, scopes, created_at, updated_at
                    )
                    SELECT $1, u.id, $3, $4, $5, $6, $7, $8, $9, $10, now(), now()
                    FROM users u WHERE u.auth_subject = $2
                    ON CONFLICT (user_id) DO UPDATE SET
                        github_user_id = EXCLUDED.github_user_id,
                        github_login = EXCLUDED.github_login,
                        access_token_enc = EXCLUDED.access_token_enc,
                        refresh_token_enc = EXCLUDED.refresh_token_enc,
                        token_expires_at = EXCLUDED.token_expires_at,
                        refresh_token_expires_at = EXCLUDED.refresh_token_expires_at,
                        installation_id = EXCLUDED.installation_id,
                        scopes = EXCLUDED.scopes,
                        updated_at = now()
                    """,
                    uuid.uuid4(),
                    user_id.value,
                    identity.github_user_id,
                    identity.github_login,
                    encrypt_secret(identity.access_token),
                    encrypt_secret(identity.refresh_token) if identity.refresh_token else None,
                    identity.token_expires_at,
                    identity.refresh_token_expires_at,
                    identity.installation_id,
                    identity.scopes,
                )
        except Exception as exc:
            raise _unavailable(f"could not store GitHub identity: {exc}") from exc

    async def delete(self, user_id: UserId) -> None:
        pool = await self._pool._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    DELETE FROM github_identities USING users u
                    WHERE github_identities.user_id = u.id AND u.auth_subject = $1
                    """,
                    user_id.value,
                )
        except Exception as exc:
            raise _unavailable(f"could not delete GitHub identity: {exc}") from exc
