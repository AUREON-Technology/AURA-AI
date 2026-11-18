"""``PostgresEventOutbox``'s claim lease against a real Postgres (audit F33).

``claim_batch`` leases a whole batch for ``_LEASE_SECONDS`` at once, and the drain
(``learning.jobs.run_outbox_drain``) dispatches the batch one entry at a time. A batch whose
dispatches took longer in total than the lease had its *unstarted* tail reclaimed by a second
worker while the first was still working through it, and those entries were delivered twice.

The lease is renewed now each time the claiming worker finishes an entry, so it only has to
outlast one dispatch, not a whole batch. Time is moved by back-dating ``claimed_at``, the one
column the lease reads, rather than by sleeping.

Opt-in (``-m load``); skips unless ``AURA_TEST_DATABASE_URL`` is set. Each test migrates its own
database with ``alembic upgrade head``.
"""

from __future__ import annotations

import os

import pytest

from tests.postgres_migrations import migrated_database

pytestmark = pytest.mark.load

_URL = os.environ.get("AURA_TEST_DATABASE_URL", "")


@pytest.fixture
async def outbox_url():
    if not _URL:
        pytest.skip("AURA_TEST_DATABASE_URL is not set")
    async with migrated_database(_URL, "outbox") as url:
        yield url


def _event():
    from aura_core.domain.events import DocumentIngested
    from aura_core.domain.identifiers import DocumentId, UserId

    return DocumentIngested(
        document_id=DocumentId("doc1"), version=1, chunks=0, source_type="upload",
        owner_id=UserId("u1"), quarantined=True,
    )


async def _age_claims(url: str, seconds: int) -> None:
    """Let ``seconds`` pass for every outstanding lease."""
    import asyncpg

    conn = await asyncpg.connect(url)
    try:
        await conn.execute(
            "UPDATE aura_event_outbox SET claimed_at = claimed_at - make_interval(secs => $1) "
            "WHERE claimed_at IS NOT NULL", seconds,
        )
    finally:
        await conn.close()


async def test_a_slow_batch_keeps_its_unstarted_entries(outbox_url) -> None:
    from aura_core.adapters.postgres_outbox import _LEASE_SECONDS, PostgresEventOutbox

    worker_a = PostgresEventOutbox(outbox_url)
    worker_b = PostgresEventOutbox(outbox_url)
    try:
        for _ in range(3):
            await worker_a.enqueue(_event())
        batch = await worker_a.claim_batch(limit=10)
        assert len(batch) == 3

        # The first dispatch takes most of the lease...
        await _age_claims(outbox_url, _LEASE_SECONDS - 50)
        await worker_a.mark_processed(batch[0].id)
        # ...and the second takes long enough that the batch as a whole has outlived it.
        await _age_claims(outbox_url, 100)
        await worker_a.mark_failed(batch[1].id, error="boom")

        # The entry worker A has not started yet is still its own; the failed one is retryable.
        reclaimed = await worker_b.claim_batch(limit=10)
        assert [e.id for e in reclaimed] == [batch[1].id]
    finally:
        await worker_a._pool.close()
        await worker_b._pool.close()


async def test_an_expired_lease_is_still_reclaimed(outbox_url) -> None:
    """Renewal is per finished entry, so a worker that died (or hung on one entry) still loses
    its claim once one dispatch outlasts the lease."""
    from aura_core.adapters.postgres_outbox import _LEASE_SECONDS, PostgresEventOutbox

    worker_a = PostgresEventOutbox(outbox_url)
    worker_b = PostgresEventOutbox(outbox_url)
    try:
        for _ in range(2):
            await worker_a.enqueue(_event())
        batch = await worker_a.claim_batch(limit=10)
        await _age_claims(outbox_url, _LEASE_SECONDS + 1)
        assert sorted(e.id for e in await worker_b.claim_batch(limit=10)) == sorted(e.id for e in batch)
    finally:
        await worker_a._pool.close()
        await worker_b._pool.close()


async def test_a_stale_worker_does_not_renew_a_claim_another_worker_took(outbox_url) -> None:
    """The renewal is fenced on the worker's own claim time: once another worker has reclaimed
    an entry, the first worker finishing something else must not extend that entry's lease."""
    import asyncpg

    from aura_core.adapters.postgres_outbox import _LEASE_SECONDS, PostgresEventOutbox

    worker_a = PostgresEventOutbox(outbox_url)
    worker_b = PostgresEventOutbox(outbox_url)
    try:
        for _ in range(2):
            await worker_a.enqueue(_event())
        batch = await worker_a.claim_batch(limit=10)
        await _age_claims(outbox_url, _LEASE_SECONDS + 1)
        taken = await worker_b.claim_batch(limit=10)
        assert len(taken) == 2

        conn = await asyncpg.connect(outbox_url)
        try:
            before = await conn.fetchval("SELECT claimed_at FROM aura_event_outbox WHERE id = $1", batch[1].id)
            await worker_a.mark_processed(batch[0].id)
            after = await conn.fetchval("SELECT claimed_at FROM aura_event_outbox WHERE id = $1", batch[1].id)
        finally:
            await conn.close()
        assert after == before
    finally:
        await worker_a._pool.close()
        await worker_b._pool.close()


async def test_a_claim_never_finished_is_forgotten_once_its_lease_runs_out(outbox_url) -> None:
    """A dispatch (or its ``mark_*``) that raised leaves its entry unfinished in this worker's
    bookkeeping. Once the lease is gone the entry is anyone's to reclaim, so the worker forgets it
    rather than holding it for its whole life."""
    from datetime import datetime, timedelta, timezone

    from aura_core.adapters import postgres_outbox
    from aura_core.adapters.postgres_outbox import PostgresEventOutbox, _Claim

    outbox = PostgresEventOutbox(outbox_url)
    long_ago = datetime.now(timezone.utc) - timedelta(seconds=postgres_outbox._LEASE_SECONDS + 60)
    stale = _Claim(claimed_at=long_ago, ids={"lost-1", "lost-2"})
    outbox._claims.update({"lost-1": stale, "lost-2": stale})

    await outbox.claim_batch(limit=10)   # nothing to claim; still tidies up

    assert "lost-1" not in outbox._claims and "lost-2" not in outbox._claims
