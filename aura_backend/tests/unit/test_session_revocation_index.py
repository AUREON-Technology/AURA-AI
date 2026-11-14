"""Session revocation finds a user's tokens by index, and a replayed refresh token kills its
family (audit FSA-015).

``revoke_all_for_uid``, ``revoke_all_devices_for_uid`` and the per-device refresh sweep each
``SCAN``ned every key in Redis and decoded every value to find the few that belonged to one
account -- O(all sessions) on every demotion, erasure or "sign out everywhere", on the same Redis
that serves rate limiting and the LLM cache. Each record is now also written to a per-user (and
per-device, per-family) set, and revocation reads the set. Records written before the index
existed are still swept by the old scan, but only until the legacy-token cut-off the module
already has.

Rotation was single-use but not replay-aware: a stolen refresh token used *after* the legitimate
client had rotated it just returned "invalid", while the thief's copy of the *next* token (if they
had it) kept working. Now a consumed token remembers its family, and presenting it again revokes
every token in that family -- the standard refresh-token-reuse defence.

Redis is stood in for by a small in-test fake with the handful of commands the module uses, so the
real Redis code path runs; the in-process fallback is tested as itself.
"""

from __future__ import annotations

import fnmatch
import json
from datetime import datetime, timedelta, timezone

import jwt as pyjwt
import pytest

from services import jwt_auth

PAST = datetime(2000, 1, 1, tzinfo=timezone.utc)
NOW = datetime.now(timezone.utc)
FUTURE = datetime(2999, 1, 1, tzinfo=timezone.utc)


class FakeRedis:
    """Strings and sets, with TTLs ignored (the tests never wait) and every SCAN counted."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.sets: dict[str, set[str]] = {}
        self.scans = 0

    # strings
    def setex(self, key, ttl, value):
        self.values[key] = value

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return None
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def getdel(self, key):
        return self.values.pop(key, None)

    def delete(self, *keys):
        removed = 0
        for key in keys:
            removed += int(self.values.pop(key, None) is not None) + int(self.sets.pop(key, None) is not None)
        return removed

    def exists(self, key):
        return int(key in self.values or key in self.sets)

    def expire(self, key, ttl):
        return 1

    def scan_iter(self, match="*", count=None):
        self.scans += 1
        for key in list(self.values) + list(self.sets):
            if fnmatch.fnmatch(key, match):
                yield key

    # sets
    def sadd(self, key, *members):
        bucket = self.sets.setdefault(key, set())
        before = len(bucket)
        bucket.update(members)
        return len(bucket) - before

    def srem(self, key, *members):
        bucket = self.sets.get(key, set())
        before = len(bucket)
        bucket.difference_update(members)
        return before - len(bucket)

    def smembers(self, key):
        return set(self.sets.get(key, set()))


@pytest.fixture
def redis(monkeypatch) -> FakeRedis:
    fake = FakeRedis()
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: fake)
    monkeypatch.setattr(jwt_auth, "_legacy_tokens_until", lambda: PAST)
    # A mature deployment: the indexed code first ran here longer ago than any record lives,
    # so nothing predating the index can still exist and the fallback scan is off.
    monkeypatch.setattr(jwt_auth, "_index_marker_noted", False)
    fake.set(jwt_auth._INDEX_SINCE_KEY, (NOW - timedelta(days=45)).isoformat())
    return fake


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setattr(jwt_auth, "get_redis_client", lambda: None)
    for name in ("_local_refresh_store", "_local_device_owners", "_local_device_meta"):
        getattr(jwt_auth, name).clear()
    jwt_auth._local_revoked_devices.clear()
    yield
    jwt_auth._local_refresh_store.clear()


def _user(uid: str) -> dict:
    return {"uid": uid, "email": f"{uid}@example.com"}


class TestRevocationReadsAnIndexNotTheWholeKeyspace:
    def test_a_user_s_refresh_tokens_are_revoked_without_a_scan(self, redis: FakeRedis) -> None:
        mine = [jwt_auth.issue_token_pair(_user("alice"))["refresh_token"] for _ in range(3)]
        theirs = jwt_auth.issue_token_pair(_user("bob"))["refresh_token"]
        assert jwt_auth.revoke_all_for_uid("alice") == 3
        assert all(jwt_auth.consume_refresh_token(t) is None for t in mine)
        assert jwt_auth.consume_refresh_token(theirs)["uid"] == "bob"
        assert redis.scans == 0, "revocation still walks the whole keyspace"

    def test_a_user_s_devices_are_found_and_revoked_without_a_scan(self, redis: FakeRedis) -> None:
        session = jwt_auth.issue_desktop_session(_user("alice"), device_id="laptop", device_name="Laptop")
        jwt_auth.issue_device_token(_user("alice"), device_id="daemon", scope=jwt_auth.HOST_AGENT_SCOPE)
        jwt_auth.issue_desktop_session(_user("bob"), device_id="bobs", device_name="Bob")
        assert {d["device_id"] for d in jwt_auth.list_devices_for_uid("alice")} == {"laptop", "daemon"}
        assert jwt_auth.revoke_all_devices_for_uid("alice") == 2
        assert jwt_auth.list_devices_for_uid("alice") == []
        assert jwt_auth.consume_refresh_token(session["refresh_token"]) is None, (
            "the device's refresh token must go with the device"
        )
        assert {d["device_id"] for d in jwt_auth.list_devices_for_uid("bob")} == {"bobs"}
        assert redis.scans == 0

    def test_records_written_before_the_index_are_still_swept_during_the_legacy_window(
        self, redis: FakeRedis, monkeypatch
    ) -> None:
        monkeypatch.setattr(jwt_auth, "_legacy_tokens_until", lambda: FUTURE)
        # The pre-index shape: a bare user_info under the refresh key, in no set.
        redis.setex(f"{jwt_auth._REFRESH_PREFIX}old-token", 100, json.dumps(_user("alice")))
        redis.setex(f"{jwt_auth._DEVICE_OWNER_PREFIX}old-device", 100, "alice")
        assert jwt_auth.revoke_all_for_uid("alice") == 1
        assert jwt_auth.consume_refresh_token("old-token") is None
        assert "old-device" in {d["device_id"] for d in jwt_auth.list_devices_for_uid("alice")}

    def test_after_the_window_only_indexed_records_count(self, redis: FakeRedis) -> None:
        redis.setex(f"{jwt_auth._REFRESH_PREFIX}old-token", 100, json.dumps(_user("alice")))
        assert jwt_auth.revoke_all_for_uid("alice") == 0
        assert redis.scans == 0

    def test_pre_index_records_are_swept_for_their_whole_lifetime_whatever_the_date(
        self, redis: FakeRedis
    ) -> None:
        """The legacy cut-off is a fixed date; a refresh token or paired daemon written the day
        before this code deployed lives 30 days from then. If the deploy lands late, the scan
        must still back the index up until every pre-index record has expired -- else demotion
        or erasure leaves a daemon signed in. The window is anchored on when the indexed code
        first ran, recorded once in Redis."""
        assert jwt_auth._legacy_tokens_until() == PAST
        del redis.values[jwt_auth._INDEX_SINCE_KEY]  # the indexed code has not run here before
        redis.setex(f"{jwt_auth._REFRESH_PREFIX}old-token", 100, json.dumps(_user("alice")))
        redis.setex(f"{jwt_auth._DEVICE_OWNER_PREFIX}old-device", 100, "alice")
        assert jwt_auth.revoke_all_for_uid("alice") == 1, "a pre-index token escaped revocation"
        assert "old-device" in {d["device_id"] for d in jwt_auth.list_devices_for_uid("alice")}
        since = datetime.fromisoformat(redis.values[jwt_auth._INDEX_SINCE_KEY])
        assert abs((since - datetime.now(timezone.utc)).total_seconds()) < 60, "marker is 'first run here'"

    def test_the_first_run_marker_is_set_once_and_kept(self, redis: FakeRedis) -> None:
        stamp = (NOW - timedelta(days=3)).isoformat()
        redis.set(jwt_auth._INDEX_SINCE_KEY, stamp)
        jwt_auth.issue_token_pair(_user("alice"))
        jwt_auth.revoke_all_for_uid("alice")
        assert redis.values[jwt_auth._INDEX_SINCE_KEY] == stamp


@pytest.mark.parametrize("store", ["redis", "local"])
class TestARepayedRefreshTokenRevokesItsFamily:
    @pytest.fixture(autouse=True)
    def _store(self, store, request):
        request.getfixturevalue(store)

    def test_rotation_without_replay_keeps_working(self) -> None:
        first = jwt_auth.issue_token_pair(_user("alice"))
        fresh = jwt_auth.consume_refresh_token(first["refresh_token"])
        second = jwt_auth.issue_token_pair(fresh)
        assert jwt_auth.consume_refresh_token(second["refresh_token"])["uid"] == "alice"

    def test_presenting_a_consumed_token_again_kills_the_whole_family(self) -> None:
        first = jwt_auth.issue_token_pair(_user("alice"))
        fresh = jwt_auth.consume_refresh_token(first["refresh_token"])
        second = jwt_auth.issue_token_pair(fresh)
        assert jwt_auth.consume_refresh_token(first["refresh_token"]) is None  # the replay
        assert jwt_auth.consume_refresh_token(second["refresh_token"]) is None, (
            "the legitimate successor must be revoked too: one of the two holders is a thief"
        )

    def test_another_user_s_family_is_untouched_by_the_replay(self) -> None:
        first = jwt_auth.issue_token_pair(_user("alice"))
        fresh = jwt_auth.consume_refresh_token(first["refresh_token"])
        jwt_auth.issue_token_pair(fresh)
        bob = jwt_auth.issue_token_pair(_user("bob"))
        jwt_auth.consume_refresh_token(first["refresh_token"])
        assert jwt_auth.consume_refresh_token(bob["refresh_token"])["uid"] == "bob"

    def test_a_desktop_session_rotates_within_its_family_too(self) -> None:
        first = jwt_auth.issue_desktop_session(_user("alice"), device_id="laptop")
        fresh = jwt_auth.consume_refresh_token(first["refresh_token"])
        second = jwt_auth.reissue_for_refreshed_identity(fresh)
        assert second["device_id"] == "laptop"
        jwt_auth.consume_refresh_token(first["refresh_token"])
        assert jwt_auth.consume_refresh_token(second["refresh_token"]) is None

    def test_the_family_marker_never_reaches_an_access_token(self) -> None:
        first = jwt_auth.issue_token_pair(_user("alice"))
        fresh = jwt_auth.consume_refresh_token(first["refresh_token"])
        second = jwt_auth.issue_token_pair(fresh)
        claims = pyjwt.decode(second["access_token"], options={"verify_signature": False})
        assert not any("family" in key for key in claims), claims
