"""Data classification of a turn's generations (ADR-030 §4, Phase 4).

The question this module answers is not "which model is best" but "who may read this prompt".
A self-hosted provider may read anything; a hosted open-weight provider -- someone else's
server -- may read only what its ceiling permits (``ProviderCapabilities.max_data_classification``,
``INTERNAL`` by default). The router excludes a provider whose ceiling is below the request's
classification, exactly as it excludes one without tool support.

Classification is set by whoever knows what the content is, as with workload hints:

* the kernel opens a :func:`data_scope` for each turn with a baseline (``INTERNAL``, or
  ``SENSITIVE`` for an Engineer turn or a turn carrying a calling system's evidence), then
  :func:`escalate`\\ s it once it sees what context it gathered -- documents, memory, goals,
  pinned documents, a session that was sensitive before;
* the tool registry escalates to ``SENSITIVE`` the moment any tool returns output, so every
  generation after that, including the agentic loop's re-planning, is bound by it.

Three properties carry the safety argument:

1. **Fail closed.** A generation with no scope and no explicit classification counts as
   ``SENSITIVE``, so a background job, the session summariser, document extraction, or anything
   written later that forgets to classify stays on local providers.
2. **Monotonic.** A scope only ever escalates. Nothing inside a turn can lower it, because
   content that has entered a context cannot be taken back out.
3. **Shared by reference.** The ambient value is a mutable :class:`DataScope`, not a level, so
   an escalation made in one task is visible to every generation made on the turn's behalf,
   including tasks spawned with ``asyncio.gather`` (they copy the context, and with it the
   reference). A nested scope, such as a delegated sub-turn, starts at its parent's level and
   hands its own escalations back when it closes.
"""

from __future__ import annotations

import contextlib
from contextvars import ContextVar
from typing import Iterator

from ..domain.llm import CompletionRequest, DataClassification

__all__ = [
    "DataScope",
    "UNSCOPED_CLASSIFICATION",
    "current_data_scope",
    "data_scope",
    "effective_classification",
    "escalate",
]

#: What a generation with no scope and no explicit classification counts as. Fail closed.
UNSCOPED_CLASSIFICATION = DataClassification.SENSITIVE

_AMBIENT: ContextVar["DataScope | None"] = ContextVar("aura_data_scope", default=None)


class DataScope:
    """The classification of one turn's content so far, plus why. Only ever escalates."""

    __slots__ = ("level", "reasons")

    def __init__(self, level: DataClassification, reason: str = "") -> None:
        self.level = level
        self.reasons: list[str] = [reason] if reason else []

    def escalate(self, level: DataClassification, reason: str = "") -> None:
        if level.rank > self.level.rank:
            self.level = level
        if reason and reason not in self.reasons:
            self.reasons.append(reason)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"DataScope({self.level.value}, {self.reasons})"


def current_data_scope() -> DataScope | None:
    return _AMBIENT.get()


def escalate(level: DataClassification, reason: str) -> None:
    """Raise the ambient scope to at least ``level``. Without a scope there is nothing to raise:
    an unscoped generation is already ``SENSITIVE``."""
    scope = _AMBIENT.get()
    if scope is not None:
        scope.escalate(level, reason)


def effective_classification(request: CompletionRequest) -> DataClassification:
    """The stricter of the request's own classification and the ambient scope's.

    A request may raise its classification above the turn's, never lower it. Content that is in
    the turn's context may be in this request's too, and the caller building one request is in
    no position to vouch otherwise. With no scope, the request's own value is trusted; with
    neither, :data:`UNSCOPED_CLASSIFICATION`.
    """
    scope = _AMBIENT.get()
    own = request.data_classification
    if scope is None:
        return own if own is not None else UNSCOPED_CLASSIFICATION
    if own is None or own.rank <= scope.level.rank:
        return scope.level
    return own


@contextlib.contextmanager
def data_scope(level: DataClassification, reason: str = "") -> Iterator[DataScope]:
    """Open a classification scope for everything awaited inside the block.

    Nested inside another scope it starts no lower than its parent, and the parent inherits its
    escalations on exit, so a delegated sub-turn can neither launder its parent's content
    through a lower level nor hide what it pulled in.
    """
    parent = _AMBIENT.get()
    scope = DataScope(level, reason)
    if parent is not None:
        scope.escalate(parent.level)
        for inherited in parent.reasons:
            scope.escalate(scope.level, inherited)
    token = _AMBIENT.set(scope)
    try:
        yield scope
    finally:
        try:
            _AMBIENT.reset(token)
        except ValueError:  # pragma: no cover - finalised in a foreign context
            _AMBIENT.set(parent)
        if parent is not None:
            for why in scope.reasons:
                parent.escalate(scope.level, why)
            parent.escalate(scope.level)
