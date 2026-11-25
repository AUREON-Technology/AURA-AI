"""The turns a user has in flight on *this* process, so one call can stop them all (ADR-041).

``POST /api/v3/agents/stop-all`` is the remote kill switch. It needs to find a user's running
turns, and nothing did: a turn is a coroutine inside a request handler, known to nobody but the
stack that is awaiting it. The kernel registers each top-level turn here when it starts and
removes it when it ends; :meth:`ActiveTurns.stop_user` cancels what is registered.

Cancellation is the asyncio kind, deliberately: it lands at whatever the turn is awaiting -- a
model call, a tool inside ``ToolRegistry``'s ``wait_for``, a host-routed call -- and unwinds
through the existing ``finally`` blocks, which is how a desktop call sends its ``cancel`` frame
(``HostAgentSession.call``). No new "stop" protocol runs beside the one that already works.

Scope, stated so nobody mistakes it for more: **one process**. A turn running in another gateway
worker is not visible here (the same limit the host-agent registry has without its relay); the
response of the route says how many it stopped, not how many exist. Delegated sub-turns are not
registered separately -- they run inside their parent's task and stop with it.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator

__all__ = ["ActiveTurns", "TrackedTurn", "StoppedTurn"]


@dataclass(slots=True)
class TrackedTurn:
    """One registered turn. ``stopped`` is set *before* the task is cancelled, so the turn's own
    ``except CancelledError`` can tell "the kill switch" from "the client went away"."""

    user_id: str
    turn_id: str
    kind: str  # "run" | "stream"
    task: "asyncio.Task | None"
    started_at: float = field(default_factory=time.monotonic)
    stopped: bool = False


@dataclass(frozen=True, slots=True)
class StoppedTurn:
    turn_id: str
    kind: str
    running_s: float


class ActiveTurns:
    def __init__(self) -> None:
        self._by_user: dict[str, list[TrackedTurn]] = {}

    @contextmanager
    def track(self, user_id: str, turn_id: str, *, kind: str) -> Iterator[TrackedTurn]:
        entry = TrackedTurn(user_id=user_id, turn_id=turn_id, kind=kind, task=asyncio.current_task())
        self._by_user.setdefault(user_id, []).append(entry)
        try:
            yield entry
        finally:
            turns = self._by_user.get(user_id)
            if turns is not None:
                try:
                    turns.remove(entry)
                except ValueError:  # pragma: no cover - removed twice
                    pass
                if not turns:
                    self._by_user.pop(user_id, None)

    def count(self, user_id: str) -> int:
        return len(self._by_user.get(user_id, ()))

    def stop_user(self, user_id: str) -> list[StoppedTurn]:
        """Cancel every turn ``user_id`` has in flight here; the list of what was cancelled.

        Idempotent: a turn already marked stopped is not counted or cancelled twice, so a second
        call (a double tap) finds nothing and returns ``[]``."""
        stopped: list[StoppedTurn] = []
        now = time.monotonic()
        for entry in list(self._by_user.get(user_id, ())):
            if entry.stopped or entry.task is None or entry.task.done():
                continue
            entry.stopped = True
            entry.task.cancel()
            stopped.append(StoppedTurn(entry.turn_id, entry.kind, round(now - entry.started_at, 3)))
        return stopped
