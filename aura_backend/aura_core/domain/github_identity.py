"""A user's connected GitHub identity (per-user OAuth connect flow).

Distinct from the deployment-wide admin token in ``config/settings.py``'s ``GitHubSettings``:
that is one shared PAT for the whole deployment, configured by an operator. This is one row per
AURA user who has authorized AURA's GitHub App, holding the token that lets the engineer agent
act on GitHub *as that user*, against exactly the repositories they granted during installation
-- not a deployment-wide allowlist.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

__all__ = ["GitHubIdentity"]


@dataclass(frozen=True, slots=True)
class GitHubIdentity:
    github_user_id: int
    github_login: str
    #: Plaintext in memory -- callers of :class:`~aura_core.ports.storage.GitHubIdentityPort`
    #: never see ciphertext. Encryption at rest is the storing adapter's concern
    #: (``adapters/postgres_github_identity.py``), the same split ``GoalSnapshot`` draws around
    #: client-side ``*_enc`` columns, just for the opposite direction: this secret both goes in
    #: and comes out in the clear, only its resting state is encrypted.
    access_token: str
    refresh_token: str | None
    token_expires_at: datetime | None
    refresh_token_expires_at: datetime | None
    installation_id: int | None
    scopes: str
    connected_at: datetime
    updated_at: datetime
