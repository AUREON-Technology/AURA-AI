"""Read/write helpers for the ``github_identities`` table, used by the OAuth connect flow
(``api/v2/github_integration.py``). Encryption of the stored tokens is handled here, at the
write boundary, using the same key ``aura_core``'s Postgres port decrypts with -- both read and
write the same row, so both must agree on how it is encrypted.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aura_core.adapters.secrets_crypto import encrypt_secret

from .models import GitHubIdentity, User


async def get_by_user(db: AsyncSession, user: User) -> GitHubIdentity | None:
    result = await db.execute(
        select(GitHubIdentity).where(GitHubIdentity.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def get_by_github_user_id(db: AsyncSession, github_user_id: int) -> GitHubIdentity | None:
    result = await db.execute(
        select(GitHubIdentity).where(GitHubIdentity.github_user_id == github_user_id)
    )
    return result.scalars().first()


async def upsert(
    db: AsyncSession,
    user: User,
    *,
    github_user_id: int,
    github_login: str,
    access_token: str,
    refresh_token: str | None,
    token_expires_at: datetime | None,
    refresh_token_expires_at: datetime | None,
    installation_id: int | None,
    scopes: str,
) -> GitHubIdentity:
    identity = await get_by_user(db, user)
    if identity is None:
        identity = GitHubIdentity(user_id=user.id)
        db.add(identity)
    identity.github_user_id = github_user_id
    identity.github_login = github_login
    identity.access_token_enc = encrypt_secret(access_token)
    identity.refresh_token_enc = encrypt_secret(refresh_token) if refresh_token else None
    identity.token_expires_at = token_expires_at
    identity.refresh_token_expires_at = refresh_token_expires_at
    identity.installation_id = installation_id
    identity.scopes = scopes
    await db.flush()
    return identity


async def delete(db: AsyncSession, user: User) -> bool:
    identity = await get_by_user(db, user)
    if identity is None:
        return False
    await db.delete(identity)
    await db.flush()
    return True


def to_status_dict(identity: GitHubIdentity) -> dict:
    """Public shape for ``GET /status`` -- never the tokens themselves."""
    return {
        "connected": True,
        "github_login": identity.github_login,
        "github_user_id": identity.github_user_id,
        "scopes": identity.scopes,
        "connected_at": identity.created_at.isoformat(),
        "token_expires_at": (
            identity.token_expires_at.isoformat() if identity.token_expires_at else None
        ),
    }


__all__ = ["get_by_user", "upsert", "delete", "to_status_dict"]
