"""`InMemoryGitHubIdentityPort` -- the reference implementation of `GitHubIdentityPort`
(per-user GitHub OAuth connect flow), exercised the same way `test_store_contract.py` exercises
the other storage ports: behaviour, not choreography."""

from __future__ import annotations

from datetime import datetime, timezone

from aura_core.adapters.memory_stores import InMemoryGitHubIdentityPort
from aura_core.domain.github_identity import GitHubIdentity
from aura_core.domain.identifiers import UserId


def _identity(**overrides) -> GitHubIdentity:
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    defaults = dict(
        github_user_id=42,
        github_login="octocat",
        access_token="ghu_abc",
        refresh_token="ghr_def",
        token_expires_at=now,
        refresh_token_expires_at=now,
        installation_id=99,
        scopes="",
        connected_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return GitHubIdentity(**defaults)


async def test_get_on_unknown_user_returns_none() -> None:
    port = InMemoryGitHubIdentityPort()
    assert await port.get(UserId("nobody")) is None


async def test_upsert_then_get_round_trips() -> None:
    port = InMemoryGitHubIdentityPort()
    identity = _identity()
    await port.upsert(UserId("alice"), identity)

    fetched = await port.get(UserId("alice"))
    assert fetched == identity


async def test_upsert_replaces_rather_than_accumulates() -> None:
    port = InMemoryGitHubIdentityPort()
    await port.upsert(UserId("alice"), _identity(github_login="old-login"))
    await port.upsert(UserId("alice"), _identity(github_login="new-login"))

    fetched = await port.get(UserId("alice"))
    assert fetched.github_login == "new-login"


async def test_identities_are_isolated_per_user() -> None:
    port = InMemoryGitHubIdentityPort()
    await port.upsert(UserId("alice"), _identity(github_login="alice-gh"))
    await port.upsert(UserId("bob"), _identity(github_login="bob-gh"))

    assert (await port.get(UserId("alice"))).github_login == "alice-gh"
    assert (await port.get(UserId("bob"))).github_login == "bob-gh"


async def test_delete_is_idempotent() -> None:
    port = InMemoryGitHubIdentityPort()
    await port.upsert(UserId("alice"), _identity())

    await port.delete(UserId("alice"))
    assert await port.get(UserId("alice")) is None
    await port.delete(UserId("alice"))  # no error the second time
