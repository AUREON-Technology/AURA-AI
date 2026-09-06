"""``services/github_oauth_state.py`` -- the CSRF state store bridging `/connect` (issues state,
authenticated) to `/callback` (consumes it, no Authorization header at all -- GitHub's redirect
carries none). No Redis in this test environment, so these exercise the in-process fallback path,
same as `store_nonce`'s equivalent tests would.
"""

from __future__ import annotations

from services.github_oauth_state import consume_state, store_state


def test_consume_returns_the_stored_user_id() -> None:
    store_state("state-1", "alice")
    assert consume_state("state-1") == "alice"


def test_consume_is_single_use() -> None:
    store_state("state-2", "alice")
    consume_state("state-2")
    assert consume_state("state-2") is None


def test_consume_of_unknown_state_returns_none() -> None:
    assert consume_state("never-issued") is None


def test_expired_state_is_not_returned() -> None:
    store_state("state-3", "alice", ttl_seconds=-1)
    assert consume_state("state-3") is None
