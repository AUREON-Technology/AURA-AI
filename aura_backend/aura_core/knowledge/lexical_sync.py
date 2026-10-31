"""Cross-worker change feed for the per-process lexical index (audit F-14, roadmap R2.4 Option A).

Every gateway worker keeps its own :class:`~aura_core.knowledge.retrieval.BM25Index`, while the
document and vector stores are shared. Without this feed, an upload served by worker A was
lexically invisible on the other workers until they restarted, and a delete or a new version
only left A's index -- the others kept serving the retired text.

**The protocol** uses nothing but the :class:`~aura_core.ports.storage.Cache` port, so it needs
no schema change and works on Redis in production and on the in-memory cache everywhere else:

- ``knowledge:lexical:generation`` is a counter. A worker that changed documents ``INCR``\\ s it
  and writes the ids it changed to ``knowledge:lexical:change:{n}``, where ``n`` is the value the
  increment returned. Atomic ``INCR`` is what gives every change its own slot across workers.
- Every worker remembers the last generation it applied (its *cursor*). Before a lexical
  search it reads the counter -- at most once per ``check_interval_s``, so the steady-state cost
  is a clock read -- and, when the counter has moved, fetches the entries in between with one
  ``MGET`` per batch and *refreshes* each named document from the document store.

**An entry names documents, not operations.** Refreshing a document means "drop whatever this
worker indexed for it, then index its active version's chunks, if it has one". That is
idempotent and independent of order, so entries can be applied twice, out of order, or after a
later change to the same document, and the index still converges to what the shared store
says. The store, not the feed, is the source of truth; the feed only says where to look.

**When the feed cannot say what changed, the worker rebuilds.** Three cases:

- the counter went *backwards* (Redis was flushed or restarted without persistence);
- the worker fell further behind than ``max_replay`` entries, or past the entries' TTL;
- an entry stays missing for longer than ``missing_grace_s`` -- a writer that died between its
  ``INCR`` and its ``SET``. A missing entry is first given that grace, because the reader can
  legitimately see a generation a split second before its writer has stored the entry.

A rebuild is the existing full :meth:`KnowledgeEngine.rebuild_lexical_index`, built off to the
side and swapped in, so queries keep using the old index until the new one is complete.

**When the cache is down, nothing is lost that matters for safety.** Reads report a miss, so a
worker simply stops catching up; deletes are still enforced on read by the active-version check
in the engine, which asks the shared store. A writer whose ``INCR`` raises keeps its pending ids
and retries them with its next publish or sync. Only the visibility of *new* uploads on other
workers waits for the cache to come back.

Redis pub/sub was considered for push invalidation and left out: the port has no subscribe
operation, a subscription is a long-lived connection per worker that the lazy-adapter pattern
does not fit, and pub/sub drops messages for a subscriber that is reconnecting -- so the counter
would still be needed to notice the gap. Polling the counter is one ``GET`` per interval per
worker, which is cheaper than what it would replace.
"""

from __future__ import annotations

import json
from typing import Iterable, Sequence

from ..observability.logging import get_logger
from ..ports.storage import Cache

__all__ = ["LexicalChangeFeed", "FeedWindow"]

logger = get_logger("knowledge.lexical_sync")


class FeedWindow:
    """What the feed holds between two generations."""

    __slots__ = ("document_ids", "first_missing", "applied_upto")

    def __init__(
        self, document_ids: list[str], first_missing: int | None, applied_upto: int
    ) -> None:
        #: Every document id named by the entries that were present, in generation order.
        self.document_ids = document_ids
        #: The lowest generation in the window whose entry was absent, if any.
        self.first_missing = first_missing
        #: The highest generation the caller may advance its cursor to: everything up to and
        #: including it was present.
        self.applied_upto = applied_upto


class LexicalChangeFeed:
    """Publishes and reads the ids of documents whose lexical index entries changed."""

    GENERATION_KEY = "knowledge:lexical:generation"
    ENTRY_PREFIX = "knowledge:lexical:change:"

    def __init__(
        self,
        cache: Cache,
        *,
        entry_ttl_s: int = 3600,
        check_interval_s: float = 0.25,
        missing_grace_s: float = 5.0,
        max_replay: int = 5000,
        fetch_batch: int = 500,
    ) -> None:
        self._cache = cache
        self.entry_ttl_s = entry_ttl_s
        self.check_interval_s = check_interval_s
        self.missing_grace_s = missing_grace_s
        self.max_replay = max_replay
        self._fetch_batch = max(1, fetch_batch)

    async def head(self) -> int | None:
        """The latest generation, ``0`` if nothing was ever published, ``None`` if unknown.

        ``None`` covers both an unreachable cache and a counter that is not there. The caller
        cannot tell those apart, so it does nothing in either case rather than rebuilding on
        every check during an outage; a counter that comes back lower than the cursor is what
        signals a reset.
        """
        raw = await self._cache.get(self.GENERATION_KEY)
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning("lexical change feed generation is not an integer")
            return None

    async def publish(self, document_ids: Iterable[str]) -> int:
        """Record that ``document_ids`` changed and return the generation used.

        Raises if the cache cannot increment (``RedisCache.incr`` raises by design); the caller
        keeps the ids and retries. The entry is written after the increment, which is the window
        ``missing_grace_s`` covers on the reading side.
        """
        ids = sorted(set(document_ids))
        generation = await self._cache.incr(self.GENERATION_KEY, ttl_s=0)
        await self._cache.set(
            f"{self.ENTRY_PREFIX}{generation}",
            json.dumps(ids, separators=(",", ":")).encode("utf-8"),
            ttl_s=self.entry_ttl_s,
        )
        return generation

    async def window(self, after: int, upto: int) -> FeedWindow:
        """Entries ``after+1 .. upto``, fetched in ``MGET`` batches."""
        document_ids: list[str] = []
        first_missing: int | None = None
        generations = list(range(after + 1, upto + 1))
        for start in range(0, len(generations), self._fetch_batch):
            batch = generations[start : start + self._fetch_batch]
            keys = [f"{self.ENTRY_PREFIX}{g}" for g in batch]
            found = await self._cache.get_many(keys)
            for generation, key in zip(batch, keys):
                raw = found.get(key)
                if raw is None:
                    if first_missing is None:
                        first_missing = generation
                    continue
                document_ids.extend(_decode(raw))
        applied_upto = upto if first_missing is None else first_missing - 1
        return FeedWindow(document_ids, first_missing, applied_upto)


def _decode(raw: bytes) -> Sequence[str]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("lexical change feed entry is not JSON; ignored")
        return ()
    if not isinstance(value, list):
        return ()
    return [str(item) for item in value]
