"""Short-term memory: working scratchpad and episodic conversation state.

The current system has **no server-side conversation state at all** -- the client re-sends the
full history on every request, so the assistant is stateless across devices, the token budget is
the client's problem, and nothing can be recalled that the client did not think to upload.

Two kinds live here because both are session-scoped and neither belongs in the vector-indexed
long-term store:

**Working memory** is the current turn's scratchpad: plan state, tool results, intermediate
conclusions. It exists for one turn and is discarded. It is in-process by design -- a turn runs
in one process, and persisting it would cost a round trip per step for data nothing else reads.

**Episodic memory** is the conversation itself: a rolling window of recent turns kept verbatim,
plus a running summary of everything older. Backed by the cache port so it survives across
requests and is shared between replicas (ADR-004: episodic memory is Redis + Postgres).
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Mapping, Sequence

from ..domain.identifiers import SessionId, UserId
from ..domain.messages import Conversation, Message, Role
from ..observability.logging import get_logger
from ..ports.storage import Cache

__all__ = ["WorkingMemory", "EpisodicMemory", "SessionState"]

logger = get_logger("memory.session")


@dataclass
class WorkingMemory:
    """Per-turn scratchpad.

    Bounded on purpose: an agent loop that appends a note per step would otherwise grow the
    context monotonically until the budget is exhausted by its own bookkeeping.
    """

    notes: list[str] = field(default_factory=list)
    facts: dict[str, Any] = field(default_factory=dict)
    tool_results: list[tuple[str, str]] = field(default_factory=list)
    max_notes: int = 20
    max_tool_results: int = 12

    def note(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        self.notes.append(cleaned)
        if len(self.notes) > self.max_notes:
            # Keep the most recent: in a plan, later notes supersede earlier ones.
            del self.notes[: len(self.notes) - self.max_notes]

    def record_tool(self, name: str, result: str) -> None:
        self.tool_results.append((name, result))
        if len(self.tool_results) > self.max_tool_results:
            del self.tool_results[: len(self.tool_results) - self.max_tool_results]

    def set(self, key: str, value: Any) -> None:
        self.facts[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.facts.get(key, default)

    def clear(self) -> None:
        self.notes.clear()
        self.facts.clear()
        self.tool_results.clear()

    def as_context(self) -> list[str]:
        return list(self.notes)

    def __bool__(self) -> bool:
        return bool(self.notes or self.facts or self.tool_results)


@dataclass(frozen=True, slots=True)
class SessionState:
    """Persisted conversation state for one session."""

    session_id: SessionId
    owner_id: UserId
    messages: tuple[Message, ...] = ()
    summary: str = ""
    turn_count: int = 0
    updated_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(
            {
                "session_id": self.session_id.value,
                "owner_id": self.owner_id.value,
                "summary": self.summary,
                "turn_count": self.turn_count,
                "updated_at": self.updated_at,
                "messages": [
                    {"role": str(m.role), "content": m.content} for m in self.messages
                ],
            }
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "SessionState | None":
        try:
            data = json.loads(raw)
            return cls(
                session_id=SessionId(data["session_id"]),
                owner_id=UserId(data["owner_id"]),
                summary=data.get("summary", ""),
                turn_count=int(data.get("turn_count", 0)),
                updated_at=float(data.get("updated_at", 0.0)),
                messages=tuple(
                    Message(role=Role(m["role"]), content=m["content"])
                    for m in data.get("messages", [])
                    # System messages are rebuilt per turn from the prompt registry; persisting
                    # them would pin a session to a stale prompt version forever.
                    if m.get("role") in ("user", "assistant")
                ),
            )
        except Exception:
            # Corrupt state is a cold start, never an exception: a bad cache entry must not
            # make a user's session permanently unusable.
            logger.warning("discarding unreadable session state")
            return None


class EpisodicMemory:
    """Rolling conversation window plus a running summary, backed by the cache port.

    ``window`` recent turns are kept verbatim because they carry conversational state -- what
    "it" refers to, what was just agreed. Older turns are folded into ``summary``, which
    preserves facts while dropping the phrasing.

    The summariser is injected rather than called directly so this class stays testable without
    a model, and so a deployment can choose to summarise with a small local model while chatting
    with a large cloud one.
    """

    _PREFIX = "session:"
    # Distinct prefixes rather than a suffix on the state key: "<state key>:lease" could be the
    # state key of a session whose id happens to end in ":lease".
    _LEASE_PREFIX = "session-lease:"
    _COMPACT_PREFIX = "session-compact:"

    def __init__(
        self,
        cache: Cache,
        *,
        window: int = 12,
        summarise_after: int = 20,
        ttl_s: int = 30 * 24 * 3600,
        summariser: Any = None,
        lease_ttl_s: int = 15,
        lease_wait_s: float = 5.0,
        compaction_ttl_s: int = 300,
    ) -> None:
        self._cache = cache
        self._window = window
        self._summarise_after = summarise_after
        self._ttl = ttl_s
        self._summariser = summariser
        self._lease_ttl = lease_ttl_s
        self._lease_wait = lease_wait_s
        self._compaction_ttl = compaction_ttl_s

    def _key(self, session_id: SessionId, owner_id: UserId | None = None) -> str:
        """The cache key for a session.

        Namespaced by owner. Keyed by session id alone, the owner check in :meth:`load` stopped
        a foreign user *reading* a conversation, but that user's turn then wrote its own fresh
        state over the same key -- erasing the owner's history by naming their session id
        (tests/security/test_idor.py). The owner's length is part of the key so no pair of
        (owner, session id) strings can collide. ``owner_id=None`` is the pre-namespacing key,
        read only as a fallback so conversations written before this change survive it.
        """
        if owner_id is None:
            return f"{self._PREFIX}{session_id.value}"
        owner = owner_id.value
        return f"{self._PREFIX}u{len(owner)}:{owner}:{session_id.value}"

    async def load(self, session_id: SessionId, owner_id: UserId) -> SessionState:
        raw = await self._cache.get(self._key(session_id, owner_id))
        if raw is None:
            # Legacy, un-namespaced state: honoured only for its own owner (checked below), and
            # never written again -- the next append lands on the namespaced key.
            raw = await self._cache.get(self._key(session_id))
        if raw is None:
            return SessionState(session_id=session_id, owner_id=owner_id)
        state = SessionState.from_json(raw)
        if state is None:
            return SessionState(session_id=session_id, owner_id=owner_id)
        if state.owner_id != owner_id:
            # A session id is not a capability. Serving another user's conversation because a
            # guessed id landed in the cache would be a direct data leak.
            logger.warning(
                "session owner mismatch; starting a fresh session",
                extra={"session": session_id.value},
            )
            return SessionState(session_id=session_id, owner_id=owner_id)
        return state

    async def append(
        self,
        session_id: SessionId,
        owner_id: UserId,
        *,
        user_message: str,
        assistant_message: str,
    ) -> SessionState:
        """Record one exchange, summarising older turns when the window overflows.

        **Concurrency (audit F-16).** This used to be load -> LLM summarise -> set with nothing
        held: two turns of one session (two tabs, a retry, a voice turn racing a text turn)
        each loaded the same state and the later ``set`` erased the other's exchange -- and
        summarisation, which can take a minute on the deployed CPU model, held that window open
        for its whole duration. Now:

        * the read-modify-write of the append itself runs under a per-(owner, session) lease
          taken through the shared cache (``incr`` is atomic in Redis, so it holds across
          workers and replicas), and that critical section contains no model call;
        * summarisation runs *outside* the lease on a snapshot of the oldest messages, then
          commits with a compare-and-set: under the lease again, it re-reads the state and only
          if those messages are still its prefix (and the summary is still the one it extended)
          does it drop that prefix and install the new summary -- keeping every message
          appended meanwhile.
        """
        key = self._key(session_id, owner_id)
        async with self._lease(key):
            state = await self.load(session_id, owner_id)
            state = SessionState(
                session_id=session_id,
                owner_id=owner_id,
                messages=(
                    *state.messages,
                    Message.user(user_message),
                    Message.assistant(assistant_message),
                ),
                summary=state.summary,
                turn_count=state.turn_count + 1,
                updated_at=time.time(),
            )
            await self._save(key, state)

        if len(state.messages) > self._summarise_after:
            state = await self._compact(session_id, owner_id, state)
        return state

    async def _save(self, key: str, state: SessionState) -> None:
        await self._cache.set(key, state.to_json().encode("utf-8"), ttl_s=self._ttl)

    @staticmethod
    def _same_messages(a: Sequence[Message], b: Sequence[Message]) -> bool:
        # Role and content are all that is persisted; `created_at` differs between a message
        # built in this process and the same message read back from the cache.
        return len(a) == len(b) and all(
            x.role == y.role and x.content == y.content for x, y in zip(a, b)
        )

    async def _compact(
        self, session_id: SessionId, owner_id: UserId, state: SessionState
    ) -> SessionState:
        """Fold the overflow into the summary without holding the session while the model runs."""
        key = self._key(session_id, owner_id)
        # Single flight: a second turn arriving while a summary is being written would only
        # summarise the same prefix again and lose the compare-and-set below. Skipping is safe:
        # the window simply stays a little longer until the next append compacts it.
        flight = f"{self._COMPACT_PREFIX}{key}"
        try:
            if await self._cache.incr(flight, ttl_s=self._compaction_ttl) != 1:
                return state
        except Exception:
            flight = ""  # no shared counter: still correct, just not deduplicated
        try:
            overflow = state.messages[: len(state.messages) - self._window]
            previous = state.summary
            summary = await self._summarise(overflow, previous)

            async with self._lease(key):
                current = await self.load(session_id, owner_id)
                if current.summary != previous or not self._same_messages(
                    current.messages[: len(overflow)], overflow
                ):
                    # The session moved on under us (cleared, or compacted by a worker whose
                    # single-flight marker had expired). Our summary describes a prefix that is
                    # no longer there; installing it would duplicate or drop turns.
                    logger.info(
                        "session changed during summarisation; discarding the summary",
                        extra={"session": session_id.value},
                    )
                    return current
                compacted = replace(
                    current,
                    messages=current.messages[len(overflow):],
                    summary=summary,
                    updated_at=time.time(),
                )
                await self._save(key, compacted)
                return compacted
        finally:
            if flight:
                await self._cache.delete(flight)

    @asynccontextmanager
    async def _lease(self, key: str) -> AsyncIterator[None]:
        """A short per-session mutual-exclusion lease over the shared cache.

        ``incr`` returning 1 means this caller created the counter and holds the lease; the TTL
        bounds how long a crashed holder can block the session. The protected section is two
        cache round trips, so waiting is brief; if the lease still cannot be had in
        ``lease_wait_s`` (or the cache cannot count at all) the write goes ahead unprotected --
        degrading to the old behaviour is better than failing the user's turn.
        """
        lease = f"{self._LEASE_PREFIX}{key}"
        held = False
        deadline = time.monotonic() + self._lease_wait
        delay = 0.005
        while True:
            try:
                held = await self._cache.incr(lease, ttl_s=self._lease_ttl) == 1
            except Exception:
                logger.warning("session lease unavailable; writing without it")
                break
            if held:
                break
            if time.monotonic() >= deadline:
                logger.warning("session lease not acquired in time; writing without it")
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.1)
        try:
            yield
        finally:
            if held:
                await self._cache.delete(lease)

    async def history(self, session_id: SessionId, owner_id: UserId) -> tuple[Conversation, str]:
        state = await self.load(session_id, owner_id)
        return Conversation.of(state.messages[-self._window * 2 :]), state.summary

    async def clear(self, session_id: SessionId, owner_id: UserId) -> None:
        """Forget the owner's state for this session (and any legacy un-namespaced state it
        owned). Takes the owner for the same reason :meth:`load` does: a session id alone must
        not let one user clear another's conversation."""
        await self._cache.delete(self._key(session_id, owner_id))
        legacy = await self._cache.get(self._key(session_id))
        if legacy is not None:
            state = SessionState.from_json(legacy)
            if state is None or state.owner_id == owner_id:
                await self._cache.delete(self._key(session_id))

    async def _summarise(self, overflow: Sequence[Message], previous: str) -> str:
        """Fold overflow turns into the running summary.

        Falls back to a truncated transcript when no summariser is configured. Losing the
        conversation entirely would be worse than keeping a crude record of it, and the fallback
        is clearly marked so it is never mistaken for a model-written summary.
        """
        transcript = "\n".join(f"{m.role}: {m.content}" for m in overflow)
        if self._summariser is None:
            merged = f"{previous}\n{transcript}" if previous else transcript
            return merged[-4000:]
        try:
            return await self._summariser(transcript, previous)
        except Exception:
            logger.warning("conversation summarisation failed; keeping truncated transcript")
            merged = f"{previous}\n{transcript}" if previous else transcript
            return merged[-4000:]


def build_session_summariser(router: Any, prompts: Any, *, model_policy: Any = None):
    """Create a summariser closure backed by the LLM router.

    Uses a deterministic, cheap generation: summarisation is a compression task where sampling
    adds nothing and non-determinism would make a session's summary depend on when it happened.
    """
    from ..domain.llm import CompletionRequest, SamplingParams
    from ..domain.messages import Conversation as _Conversation, Message as _Message

    async def summarise(transcript: str, previous: str) -> str:
        rendered = prompts.render(
            "task.conversation_summary",
            {"conversation": transcript, "previous_summary": previous},
        )
        request = CompletionRequest(
            conversation=_Conversation.of([_Message.user(rendered.text)]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=512),
            policy=model_policy,
        )
        response = await router.complete(request)
        return response.text.strip()

    return summarise


def merge_context(state: SessionState, extra: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "turn_count": state.turn_count,
        "has_summary": bool(state.summary),
        "window_messages": len(state.messages),
    }
    payload.update(extra or {})
    return payload
