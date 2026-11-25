"""What every ``CloudSandboxStore`` must do. Run against the in-memory adapter in the default
suite and against Postgres in ``tests/load``.

The store is what lets several web workers share one cloud workspace, so the cases that matter are
the refusals: two sandboxes for one key, a save while a command runs, a command during a save.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
LATER = NOW + timedelta(minutes=10)
MUCH_LATER = NOW + timedelta(hours=2)
_SAVE = "ws-" + "a" * 32
_OTHER = "ws-" + "b" * 32


def _key() -> str:
    return uuid.uuid4().hex


class CloudSandboxStoreCases:
    """Subclasses provide a ``store`` fixture."""

    async def _attached(self, store) -> str:
        key = _key()
        await store.ensure(key, "workspace", at=NOW)
        assert await store.attach(key, "box-1", expected_checkpoint=None, at=NOW)
        return key

    async def test_ensure_creates_once_and_keeps_what_is_there(self, store) -> None:
        key = await self._attached(store)

        again = await store.ensure(key, "workspace", at=LATER)

        assert (again.key, again.kind, again.sandbox_id) == (key, "workspace", "box-1")
        assert again.booted_at == NOW

    async def test_an_unknown_key_reads_as_nothing(self, store) -> None:
        assert await store.get(_key()) is None
        assert not await store.attach(_key(), "box", expected_checkpoint=None, at=NOW)

    async def test_keys_are_listed_by_kind(self, store) -> None:
        task, workspace = _key(), _key()
        await store.ensure(task, "task", at=NOW)
        await store.ensure(workspace, "workspace", at=NOW)

        listed = await store.list_keys(kind="workspace")

        assert workspace in listed and task not in listed

    async def test_one_key_gets_one_sandbox(self, store) -> None:
        key = await self._attached(store)

        assert not await store.attach(key, "box-2", expected_checkpoint=None, at=NOW)

        assert (await store.get(key)).sandbox_id == "box-1"

    async def test_concurrent_attaches_have_one_winner(self, store) -> None:
        key = _key()
        await store.ensure(key, "workspace", at=NOW)

        won = await asyncio.gather(*(
            store.attach(key, f"box-{n}", expected_checkpoint=None, at=NOW) for n in range(8)
        ))

        assert won.count(True) == 1

    async def test_a_sandbox_from_an_older_save_is_not_attached(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)
        assert await store.commit_save(key, _SAVE, used_bytes=10)
        assert await store.detach(key, "box-1")

        assert not await store.attach(key, "box-2", expected_checkpoint=None, at=NOW)
        assert await store.attach(key, "box-2", expected_checkpoint=_SAVE, at=NOW)

    async def test_detach_only_forgets_the_named_sandbox(self, store) -> None:
        key = await self._attached(store)

        assert not await store.detach(key, "box-2")
        assert await store.detach(key, "box-1")

        record = await store.get(key)
        assert (record.sandbox_id, record.booted_at) == (None, None)

    async def test_no_save_while_a_command_runs(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_use(key, "box-1", at=NOW, until=LATER)
        assert await store.begin_use(key, "box-1", at=NOW, until=LATER)

        assert not await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)
        await store.end_use(key, "box-1")
        assert not await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)
        await store.end_use(key, "box-1")
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

    async def test_a_command_whose_worker_died_stops_counting(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_use(key, "box-1", at=NOW, until=LATER)

        assert await store.begin_save(key, "box-1", _SAVE, at=MUCH_LATER, until=MUCH_LATER)

    async def test_an_expired_count_starts_again(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_use(key, "box-1", at=NOW, until=LATER)

        assert await store.begin_use(
            key, "box-1", at=MUCH_LATER, until=MUCH_LATER + timedelta(minutes=1))

        assert (await store.get(key)).in_use == 1

    async def test_the_count_never_goes_below_zero(self, store) -> None:
        key = await self._attached(store)
        await store.end_use(key, "box-1")
        assert (await store.get(key)).in_use == 0

    async def test_use_is_only_granted_on_the_attached_sandbox(self, store) -> None:
        key = await self._attached(store)

        assert not await store.begin_use(key, "box-2", at=NOW, until=LATER)
        assert not await store.begin_save(key, "box-2", _SAVE, at=NOW, until=LATER)

    async def test_no_command_or_second_save_during_a_save(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        assert not await store.begin_use(key, "box-1", at=NOW, until=LATER)
        assert not await store.begin_save(key, "box-1", _OTHER, at=NOW, until=LATER)
        record = await store.get(key)
        assert (record.saving_name, record.saving_until) == (_SAVE, LATER)

    async def test_a_save_whose_worker_died_can_be_taken_over(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        assert await store.begin_use(key, "box-1", at=MUCH_LATER, until=MUCH_LATER)

    async def test_a_commit_records_the_save_and_keeps_the_claim(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        assert not await store.commit_save(key, _OTHER, used_bytes=1)
        assert await store.commit_save(key, _SAVE, used_bytes=123)

        record = await store.get(key)
        assert (record.checkpoint_name, record.used_bytes) == (_SAVE, 123)
        assert not await store.begin_use(key, "box-1", at=NOW, until=LATER)

    async def test_a_commit_without_a_size_keeps_the_known_one(self, store) -> None:
        key = await self._attached(store)
        await store.record_usage(key, 77)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        assert await store.commit_save(key, _SAVE, used_bytes=None)

        assert (await store.get(key)).used_bytes == 77

    async def test_releasing_a_save_leaves_the_last_checkpoint(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        await store.release_save(key, _SAVE)

        record = await store.get(key)
        assert (record.saving_name, record.saving_until, record.checkpoint_name) == (None,) * 3
        assert await store.begin_use(key, "box-1", at=NOW, until=LATER)

    async def test_detach_clears_the_claims_on_that_sandbox(self, store) -> None:
        key = await self._attached(store)
        assert await store.begin_save(key, "box-1", _SAVE, at=NOW, until=LATER)

        assert await store.detach(key, "box-1")

        record = await store.get(key)
        assert (record.saving_name, record.saving_until, record.in_use) == (None, None, 0)

    async def test_usage_is_remembered(self, store) -> None:
        key = await self._attached(store)

        await store.record_usage(key, 5 * 1024 ** 3)

        assert (await store.get(key)).used_bytes == 5 * 1024 ** 3

    async def test_delete_only_removes_what_the_caller_cleaned_up(self, store) -> None:
        key = await self._attached(store)

        assert not await store.delete(key, sandbox_id=None, checkpoint_name=None)
        assert not await store.delete(key, sandbox_id="box-1", checkpoint_name=_SAVE)
        assert await store.get(key) is not None
        assert await store.delete(key, sandbox_id="box-1", checkpoint_name=None)
        assert await store.get(key) is None
