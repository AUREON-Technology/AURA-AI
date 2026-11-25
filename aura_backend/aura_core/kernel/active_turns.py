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

__all__ = ["ActiveTurns", "TrackedTurn", "StoppedTurn", "MAX_PAUSE_S", "MAX_REDIRECT_CHARS", "MAX_REDIRECTS"]

#: A pause that nobody resumes ends by itself, so a forgotten phone cannot hold a turn (and the
#: connection and sandbox behind it) forever. The loop then carries on and says so.
MAX_PAUSE_S = 900.0
MAX_REDIRECT_CHARS = 1000
MAX_REDIRECTS = 5


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
    #: Pause / redirect (ADR-059). Cooperative: honoured at the agent loop's checkpoints between
    #: passes, never in the middle of a tool call, so a pause cannot leave a half-applied write.
    paused: bool = False
    paused_at: float = 0.0
    redirects: list[str] = field(default_factory=list)
    resumed: "asyncio.Event" = field(default_factory=asyncio.Event)


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

    # -- pause / resume / redirect (ADR-059) ------------------------------------------------

    def _live(self, user_id: str) -> list[TrackedTurn]:
        return [e for e in self._by_user.get(user_id, ()) if not e.stopped and e.task is not None and not e.task.done()]

    def pause_user(self, user_id: str) -> int:
        """Ask every running turn of ``user_id`` to hold at its next checkpoint; how many were asked.
        Idempotent: an already-paused turn is not counted again."""
        n = 0
        for entry in self._live(user_id):
            if not entry.paused:
                entry.paused, entry.paused_at = True, time.monotonic()
                entry.resumed.clear()
                n += 1
        return n

    def resume_user(self, user_id: str) -> int:
        n = 0
        for entry in self._live(user_id):
            if entry.paused:
                entry.paused = False
                entry.resumed.set()
                n += 1
        return n

    def redirect_user(self, user_id: str, instruction: str) -> int:
        """Queue ``instruction`` for every running turn of ``user_id`` (also resumes a paused one: a
        redirect is an answer to "what now?"). At the next checkpoint the loop replans with it as the
        person's own instruction. Bounded: at most :data:`MAX_REDIRECTS` waiting per turn."""
        text = " ".join(str(instruction).split())[:MAX_REDIRECT_CHARS]
        if not text:
            return 0
        n = 0
        for entry in self._live(user_id):
            if len(entry.redirects) < MAX_REDIRECTS:
                entry.redirects.append(text)
                n += 1
            if entry.paused:
                entry.paused = False
                entry.resumed.set()
        return n

    def entry_for(self, user_id: str, turn_id: str) -> "TrackedTurn | None":
        for entry in self._by_user.get(user_id, ()):
            if entry.turn_id == turn_id:
                return entry
        return None

    async def hold(self, user_id: str, turn_id: str) -> bool:
        """Wait while the turn is paused; true when an instruction is waiting for it (the caller then
        stops what it is doing and lets the loop pick the instruction up). Does not drain."""
        entry = self.entry_for(user_id, turn_id)
        if entry is None:
            return False
        if entry.paused:
            try:
                await asyncio.wait_for(entry.resumed.wait(), timeout=MAX_PAUSE_S)
            except asyncio.TimeoutError:
                entry.paused = False
                entry.resumed.set()
        return bool(entry.redirects)

    async def checkpoint(self, user_id: str, turn_id: str) -> tuple[list[str], str | None]:
        """Called by the agent loop between passes. Holds while the turn is paused (at most
        :data:`MAX_PAUSE_S`), then returns ``(redirects, note)``: the instructions queued for this turn
        (drained) and, if a pause timed out, a sentence saying so. A turn nobody controls returns
        ``([], None)`` at once."""
        entry = self.entry_for(user_id, turn_id)
        if entry is None:
            return [], None
        note: str | None = None
        if entry.paused:
            try:
                await asyncio.wait_for(entry.resumed.wait(), timeout=MAX_PAUSE_S)
            except asyncio.TimeoutError:
                entry.paused = False
                entry.resumed.set()
                note = f"The pause lasted {int(MAX_PAUSE_S // 60)} minutes with no answer, so the run carried on."
        taken, entry.redirects = entry.redirects, []
        return taken, note
