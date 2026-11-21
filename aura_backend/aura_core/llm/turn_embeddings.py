"""One query embedding per text per turn (KAN-111).

Memory recall, dense retrieval and the semantic cache (its lookup *and* its store) each embedded
the turn's message on their own: four calls to the embedding endpoint for one vector. Inside a
turn, :func:`embed_query` shares the vector -- and, when two of those callers run concurrently
(recall and retrieval do), the in-flight call itself, so they do not race to embed it twice.

Keyed by the embedder's identity (name, model, dimension) *and* the exact text, so two embedding
spaces never share a vector and a rewritten query is embedded for what it says. Only query-side
embeddings are shared; ingestion and memory writes embed passages and are left alone. Outside an
open :func:`embedding_memo_scope` this is exactly ``embedder.embed([text], for_query=True)``.
"""

from __future__ import annotations

import asyncio
import contextlib
from contextvars import ContextVar
from typing import Any, Iterator

from ..domain.llm import EmbeddingResult

__all__ = ["embed_query", "embedding_memo_scope"]

_Key = tuple[str, str, int, str]

_MEMO: ContextVar["dict[_Key, asyncio.Future[EmbeddingResult]] | None"] = ContextVar(
    "aura_turn_embeddings", default=None
)


@contextlib.contextmanager
def embedding_memo_scope() -> Iterator[None]:
    """Share query embeddings among everything awaited inside the block (one turn)."""
    parent = _MEMO.get()
    token = _MEMO.set({})
    try:
        yield
    finally:
        try:
            _MEMO.reset(token)
        except ValueError:  # pragma: no cover - a stream finalised in a foreign context
            _MEMO.set(parent)


async def embed_query(embedder: Any, text: str) -> EmbeddingResult:
    """``embedder.embed([text], for_query=True)``, at most once per turn for the same text.

    A failure is shared by whoever was waiting on that call and is then forgotten, so a later
    caller in the same turn tries again rather than inheriting a transient error.
    """
    memo = _MEMO.get()
    if memo is None:
        return await embedder.embed([text], for_query=True)
    key: _Key = (
        str(getattr(embedder, "name", "")),
        str(getattr(embedder, "model_id", "")),
        int(getattr(embedder, "dimension", 0) or 0),
        text,
    )
    future = memo.get(key)
    if future is None:
        future = asyncio.ensure_future(embedder.embed([text], for_query=True))
        memo[key] = future

        def forget_failure(done: "asyncio.Future[EmbeddingResult]") -> None:
            if (done.cancelled() or done.exception() is not None) and memo.get(key) is done:
                del memo[key]

        future.add_done_callback(forget_failure)
    # Shielded: one caller giving up (its turn cancelled) must not cancel the shared call.
    return await asyncio.shield(future)
