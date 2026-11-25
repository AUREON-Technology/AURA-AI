"""The turns a user has in flight on *this* process, so one call can stop them all (ADR-041).

``POST /api/v3/agents/stop-all`` is the remote kill switch. It needs to find a user's running
turns, and nothing did: a turn is a coroutine inside a request handler, known to nobody but the
stack that is awaiting it. The kernel registers each top-level turn here when it starts and
removes it when it ends; :meth:`ActiveTurns.stop_user` cancels what is registered.

Cancellation is the asyncio kind, deliberately: it lands at whatever the turn is awaiting -- a
model call, a tool inside ``ToolRegistry``'s ``wait_for``, a host-routed call -- and unwinds
through the existing ``finally`` blocks, which is how a desktop call sends its ``cancel`` frame
(``HostAgentSession.call``). No new "stop" protocol runs beside the one that already works.

Scope: a turn is cancelled by the process that runs it. With a shared cache (``shared``, the same
Redis every worker uses) a stop / pause / resume / redirect is also appended to a small per-user
event list and every worker's turns of that user pick it up within ``POLL_S`` (ADR-059 update); without
one, only this process's turns are reached, and the routes say so. The response says how many turns
*this* process stopped, not how many exist elsewhere. Delegated sub-turns are not registered
separately -- they run inside their parent's task and stop with it.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator

__all__ = ["ActiveTurns", "TrackedTurn", "StoppedTurn", "MAX_PAUSE_S", "MAX_REDIRECT_CHARS", "MAX_REDIRECTS"]

#: A pause that nobody resumes ends by itself, so a forgotten phone cannot hold a turn (and the
#: connection and sandbox behind it) forever. The loop then carries on and says so.
MAX_PAUSE_S = 900.0

#: How often a running turn looks for a control event another worker appended. One small Redis read per
#: running turn per interval; nothing is read when no shared cache is configured.
POLL_S = 1.0
#: Events kept per user and how long they live. A control event only matters while the turn it targets is
#: running, so a short TTL bounds how long an instruction's text sits in the cache.
_EVENT_CAP = 50
_EVENT_TTL_S = 600
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
    #: Control-event ids this turn has already applied or that predate it (so a stop pressed before the
    #: turn began never kills it). Only used with a shared cache.
    seen: set = field(default_factory=set)
    watcher: "asyncio.Task | None" = None


@dataclass(frozen=True, slots=True)
class StoppedTurn:
    turn_id: str
    kind: str
    running_s: float


class ActiveTurns:
    def __init__(self, shared: Any = None) -> None:
        self._by_user: dict[str, list[TrackedTurn]] = {}
        #: A ``Cache`` (``ports/storage.py``) shared by every gateway worker, or ``None`` (one process).
        self.shared = shared

    # -- cross-worker events (only with ``shared``) --------------------------------------------

    @staticmethod
    def _key(user_id: str) -> str:
        return f"aura:agentctl:{user_id}:v1"

    async def _publish(self, user_id: str, op: str, text: str = "") -> bool:
        """Append a control event for ``user_id``'s turns on other workers; whether it was written. Never
        raises: the local effect has already happened and a cache outage must not undo it."""
        if self.shared is None:
            return False
        event = {"id": uuid.uuid4().hex, "op": op, "text": text}
        try:
            await self.shared.append_capped(
                self._key(user_id), json.dumps(event).encode("utf-8"), max_len=_EVENT_CAP, ttl_s=_EVENT_TTL_S)
            return True
        except Exception:
            return False

    async def _events(self, user_id: str) -> list[dict[str, Any]]:
        try:
            raw = await self.shared.get_list(self._key(user_id))
        except Exception:
            return []
        out: list[dict[str, Any]] = []
        for item in raw:
            try:
                data = json.loads(item)
            except (ValueError, TypeError):
                continue
            if isinstance(data, dict) and isinstance(data.get("id"), str):
                out.append(data)
        return out

    async def _watch(self, entry: "TrackedTurn") -> None:
        """Apply, in order, every control event for this turn's user that arrived after the turn began."""
        while True:
            try:
                await asyncio.sleep(POLL_S)
                for event in await self._events(entry.user_id):
                    if event["id"] in entry.seen:
                        continue
                    entry.seen.add(event["id"])
                    self._apply(entry, str(event.get("op")), str(event.get("text") or ""))
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover - a watcher must never take the turn down
                continue

    def _apply(self, entry: "TrackedTurn", op: str, text: str) -> None:
        if entry.stopped or entry.task is None or entry.task.done():
            return
        if op == "stop":
            entry.stopped = True
            entry.task.cancel()
        elif op == "pause" and not entry.paused:
            entry.paused, entry.paused_at = True, time.monotonic()
            entry.resumed.clear()
        elif op == "resume" and entry.paused:
            entry.paused = False
            entry.resumed.set()
        elif op == "redirect" and text:
            if len(entry.redirects) < MAX_REDIRECTS:
                entry.redirects.append(" ".join(text.split())[:MAX_REDIRECT_CHARS])
            if entry.paused:
                entry.paused = False
                entry.resumed.set()

    async def _start_watching_task(self, entry: "TrackedTurn") -> None:
        # Everything already in the list predates this turn: remember it so it is never applied to it.
        for event in await self._events(entry.user_id):
            entry.seen.add(event["id"])
        await self._watch(entry)

    # -- the signalling API the routes call (local effect first, then the other workers) ---------

    async def signal(self, user_id: str, op: str, text: str = "") -> dict[str, Any]:
        """Apply ``op`` ("stop" | "pause" | "resume" | "redirect") to this process's turns and publish it
        for the other workers. Returns ``{"local": <turns reached here>, "published": bool}``."""
        if op == "stop":
            local = len(self.stop_user(user_id))
        elif op == "pause":
            local = self.pause_user(user_id)
        elif op == "resume":
            local = self.resume_user(user_id)
        else:
            local = self.redirect_user(user_id, text)
        return {"local": local, "published": await self._publish(user_id, op, text)}

    @contextmanager
    def track(self, user_id: str, turn_id: str, *, kind: str) -> Iterator[TrackedTurn]:
        entry = TrackedTurn(user_id=user_id, turn_id=turn_id, kind=kind, task=asyncio.current_task())
        self._by_user.setdefault(user_id, []).append(entry)
        if self.shared is not None and entry.task is not None:
            # The cursor read and the poller run as their own task, so a slow cache never delays the turn.
            entry.watcher = asyncio.ensure_future(self._start_watching_task(entry))
        try:
            yield entry
        finally:
            if entry.watcher is not None:
                entry.watcher.cancel()
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
