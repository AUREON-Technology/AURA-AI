"""Per-turn memo of lookups whose answer one turn does not need twice (KAN-109).

A turn used to ask the same questions several times: whether the user has a paired desktop
(a Redis round trip through the host-agent relay, once for the tool list and again for the
system prompt), what its workload hint is (re-scoring the message's complexity three times),
and which registry model serves it. Each answer is fixed for the length of a turn, or close
enough that the second asking only adds latency.

The memo is ambient (a ``ContextVar``) rather than threaded through every signature, so the
kernel's stages and the composition root's tool hooks can share it without changing the ports
between them. It is opened once per turn by the kernel and inherited by every task the turn
starts; a delegated sub-turn opens its own. Outside a turn there is no memo and every lookup
runs as it always did.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from ..llm.turn_embeddings import embedding_memo_scope

__all__ = ["TurnLookups", "current_turn_lookups", "turn_lookup_scope"]


@dataclass(slots=True)
class TurnLookups:
    """What this turn has already resolved. Mutable: filled lazily by the first asker."""

    #: ``user_id.value`` -> whether a desktop session resolved for that user this turn.
    desktop: dict[str, bool] = field(default_factory=dict)
    #: ``(request, hint)``: the request object the hint was computed for. Keyed by identity, so a
    #: resumed confirmation (a new request carrying the held turn's persona) is recomputed.
    workload: tuple[Any, Any] | None = None
    #: ``user_id.value`` -> the registry model tag this turn's generations use (or ``None``).
    assigned_model: dict[str, str | None] = field(default_factory=dict)
    #: The private context sources this turn skipped because no provider could take them
    #: (KAN-112); empty when nothing was skipped.
    skipped_private_context: tuple[str, ...] = ()
    #: What the person wrote, as the kernel guarded it: this turn's message and the session's
    #: earlier user messages (audit B5). ``remember_fact`` stores only facts these restate. A
    #: delegated sub-turn inherits its parent's: its own "message" is a brief the parent's model
    #: wrote, not something the person said.
    user_statements: tuple[str, ...] = ()


_CURRENT: ContextVar[TurnLookups | None] = ContextVar("aura_turn_lookups", default=None)


def current_turn_lookups() -> TurnLookups | None:
    """The running turn's memo, or ``None`` outside a turn."""
    return _CURRENT.get()


@contextlib.contextmanager
def turn_lookup_scope() -> Iterator[TurnLookups]:
    """Open a fresh memo for everything awaited inside the block.

    Also the turn's query-embedding memo (KAN-111): memory recall, retrieval and the semantic
    cache then embed the message once between them.
    """
    parent = _CURRENT.get()
    lookups = TurnLookups(
        user_statements=parent.user_statements if parent is not None else ()
    )
    token = _CURRENT.set(lookups)
    try:
        with embedding_memo_scope():
            yield lookups
    finally:
        try:
            _CURRENT.reset(token)
        except ValueError:  # pragma: no cover - a stream finalised in a foreign context
            _CURRENT.set(parent)
