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
import weakref
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Mapping, Sequence

from ..domain.identifiers import SessionId, UserId
from ..domain.messages import Conversation, Message, Role
from ..observability.logging import get_logger
from ..ports.storage import Cache

__all__ = [
    "WorkingMemory",
    "EpisodicMemory",
    "SessionState",
    "SessionHistory",
    "FALLBACK_SUMMARY_MARKER",
    "is_tainted",
]

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
    #: How many messages have been folded into ``summary`` over the session's life. It only
    #: grows, so it versions the summary: a summary computed against one value applies only if
    #: no other summary landed while the model was writing it (see ``EpisodicMemory.append``).
    folded: int = 0
    #: Whether ``summary`` was built from any message of a tainted turn, or from a summary that
    #: was (KAN-101). The summary is a model's rewrite of that text -- or the raw transcript --
    #: so an instruction a quarantined document or untrusted tool output planted can survive
    #: into it. Sticky: folding more messages into a tainted summary keeps it tainted.
    summary_tainted: bool = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "session_id": self.session_id.value,
                "owner_id": self.owner_id.value,
                "summary": self.summary,
                "summary_tainted": self.summary_tainted,
                "turn_count": self.turn_count,
                "updated_at": self.updated_at,
                "folded": self.folded,
                "messages": [
                    {
                        "role": str(m.role),
                        "content": m.content,
                        # Only written when set, so untainted state keeps its old shape.
                        **({"tainted": True} if is_tainted(m) else {}),
                    }
                    for m in self.messages
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
                folded=int(data.get("folded", 0)),
                summary_tainted=bool(data.get("summary_tainted", False)),
                messages=tuple(
                    _marked(
                        Message(role=Role(m["role"]), content=m["content"]),
                        bool(m.get("tainted", False)),
                    )
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


#: Message metadata key recording that the message came from a tainted turn (KAN-101).
TAINTED_METADATA_KEY = "tainted"


def is_tainted(message: Message) -> bool:
    """Whether ``message`` was recorded from a tainted turn."""
    return bool(message.metadata.get(TAINTED_METADATA_KEY, False))


def _marked(message: Message, tainted: bool) -> Message:
    return message.with_metadata(**{TAINTED_METADATA_KEY: True}) if tainted else message


@dataclass(frozen=True, slots=True)
class SessionHistory:
    """What a turn reads from the session: verbatim messages, the summary, and their taint.

    ``tainted`` is what the kernel acts on (ADR-006, KAN-101): content written while some turn's
    context held quarantined or untrusted DATA comes back on the next turn's prompt -- verbatim,
    or folded into the summary -- so the turn reading it is tainted exactly as if it had
    retrieved that content itself.
    """

    conversation: Conversation
    summary: str = ""
    summary_tainted: bool = False

    @property
    def tainted(self) -> bool:
        return (self.summary_tainted and bool(self.summary)) or any(
            is_tainted(m) for m in self.conversation
        )


def _same_prefix(messages: Sequence[Message], prefix: Sequence[Message]) -> bool:
    """Whether ``messages`` begins with ``prefix``, by role and content -- the only fields
    session state persists (``created_at`` is re-stamped on every load)."""
    if len(messages) < len(prefix):
        return False
    return all(
        (a.role, a.content) == (b.role, b.content) for a, b in zip(messages, prefix)
    )


class EpisodicMemory:
    """Rolling conversation window plus a running summary, backed by the cache port.

    ``window`` recent turns are kept verbatim because they carry conversational state -- what
    "it" refers to, what was just agreed. Older turns are folded into ``summary``, which
    preserves facts while dropping the phrasing.

    The summariser is injected rather than called directly so this class stays testable without
    a model, and so a deployment can choose to summarise with a small local model while chatting
    with a large cloud one.

    **Writes are serialised per (owner, session), and the model is never called while holding
    that.** Append used to load the state, await the summariser, and write back what it had
    loaded -- so every exchange another turn appended while the model was thinking (seconds to
    minutes on a CPU-only model) was silently overwritten (F-16). Now a short critical section
    does load-append-write, the summary is computed outside it, and a second critical section
    applies it only if the state is still the one it was computed from (see :meth:`append`).

    The critical section is an in-process ``asyncio.Lock`` plus, for other workers and replicas,
    a lease taken with the cache port's atomic ``incr`` (a real ``INCR`` on Redis). Both are
    held for two cache round trips, never for a model call. If the cache cannot arbitrate
    (Redis down, or a lease still held after ``lease_wait_s``) the write proceeds under the
    in-process lock alone: recording the user's exchange matters more than the rare cross-worker
    race that degrades to, and with Redis down there is no shared state to race over anyway.
    """

    _PREFIX = "session:"
    # Deliberately not under _PREFIX: no session id can make its data key equal a lease key,
    # so releasing a lease can never delete a conversation.
    _LEASE_PREFIX = "session-lease:"

    def __init__(
        self,
        cache: Cache,
        *,
        window: int = 12,
        summarise_after: int = 20,
        ttl_s: int = 30 * 24 * 3600,
        summariser: Any = None,
        lease_ttl_s: int = 10,
        lease_wait_s: float = 12.0,
    ) -> None:
        self._cache = cache
        self._window = window
        self._summarise_after = summarise_after
        self._ttl = ttl_s
        self._summariser = summariser
        # The lease outlives a crashed holder by at most its TTL; waiting a little longer than
        # that means a stuck lease expires before a waiter gives up on it.
        self._lease_ttl = lease_ttl_s
        self._lease_wait = lease_wait_s
        # Weak values: a lock exists only while some append holds or awaits it, so one entry
        # per session ever seen cannot accumulate for the life of the process.
        self._locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )
        # Sessions this process is already summarising. A turn that overflows while a summary
        # is in flight just appends; the in-flight one trims what it covers and the next turn
        # summarises any remainder -- rather than paying the model twice for the same prefix.
        self._summarising: set[str] = set()

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
        tainted: bool = False,
    ) -> SessionState:
        """Record one exchange, summarising older turns when the window overflows.

        ``tainted`` says the turn that produced the exchange was tainted (ADR-006). Both its
        messages carry the mark, and a summary that folds either of them in inherits it
        (KAN-101), so the taint outlives the verbatim window.

        Three steps, so that no concurrent append is lost and none waits on the model:

        1. Under the per-session lock: load, append the exchange, write. The exchange is durable
           before any summarisation starts.
        2. Unlocked: summarise the overflow (everything but the last ``window`` messages).
        3. Under the lock again, compare-and-set: re-load, and apply the summary only if no
           other summary landed meanwhile (``folded`` unchanged) and the state still begins with
           the messages that were summarised. Then drop exactly those from the front, keeping
           every message appended since step 1. Otherwise the summary is stale -- another worker
           folded first, or the session was cleared -- and is discarded rather than written over
           newer state; the messages stay verbatim and the next turn summarises them.
        """
        key = self._key(session_id, owner_id)
        async with self._exclusive(key):
            loaded = await self.load(session_id, owner_id)
            state = SessionState(
                session_id=session_id,
                owner_id=owner_id,
                messages=(
                    *loaded.messages,
                    _marked(Message.user(user_message), tainted),
                    _marked(Message.assistant(assistant_message), tainted),
                ),
                summary=loaded.summary,
                turn_count=loaded.turn_count + 1,
                updated_at=time.time(),
                folded=loaded.folded,
                summary_tainted=loaded.summary_tainted,
            )
            await self._write(key, state)

        if len(state.messages) <= self._summarise_after or key in self._summarising:
            return state

        self._summarising.add(key)
        try:
            overflow = state.messages[: len(state.messages) - self._window]
            summary = await self._summarise(overflow, state.summary)

            async with self._exclusive(key):
                current = await self.load(session_id, owner_id)
                if current.folded != state.folded or not _same_prefix(current.messages, overflow):
                    logger.info(
                        "discarding a stale session summary",
                        extra={"session": session_id.value},
                    )
                    return current
                folded_now = current.messages[: len(overflow)]
                applied = replace(
                    current,
                    messages=current.messages[len(overflow):],
                    summary=summary,
                    folded=current.folded + len(overflow),
                    updated_at=time.time(),
                    # A summary of a tainted summary, or of any tainted message, is tainted --
                    # whether a model wrote it or it is the fallback transcript excerpt.
                    summary_tainted=(
                        current.summary_tainted
                        or any(is_tainted(m) for m in (*folded_now, *overflow))
                    ),
                )
                await self._write(key, applied)
                return applied
        finally:
            self._summarising.discard(key)

    async def _write(self, key: str, state: SessionState) -> None:
        await self._cache.set(key, state.to_json().encode("utf-8"), ttl_s=self._ttl)

    @asynccontextmanager
    async def _exclusive(self, key: str) -> AsyncIterator[None]:
        """Hold the session's write lock: in-process first, then the cross-process lease."""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        async with lock:
            lease = await self._acquire_lease(key)
            try:
                yield
            finally:
                if lease is not None:
                    await self._cache.delete(lease)

    async def _acquire_lease(self, key: str) -> str | None:
        """Take the cross-process lease for ``key``; ``None`` if the cache cannot arbitrate.

        ``incr`` is atomic across replicas and creates the counter with a TTL, so exactly one
        caller sees 1 and holds it until it deletes the key -- or until the TTL frees it, if the
        holder died. Waiters' increments do not extend the TTL.
        """
        lease = f"{self._LEASE_PREFIX}{key}"
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._lease_wait
        delay = 0.01
        while True:
            try:
                if await self._cache.incr(lease, ttl_s=self._lease_ttl) == 1:
                    return lease
            except Exception:
                logger.warning("session lease unavailable; serialising in-process only")
                return None
            if loop.time() >= deadline:
                logger.warning("session lease not acquired in time; proceeding without it")
                return None
            await asyncio.sleep(delay)
            delay = min(delay * 2, 0.2)

    async def history(self, session_id: SessionId, owner_id: UserId) -> tuple[Conversation, str]:
        """Every message not yet folded into the summary, and the summary.

        All of them, not a tail: anything held here is by definition not in the summary, so a
        message cut here would be in neither and the context engine could not even say it was
        missing (KAN-100). The state is bounded by ``summarise_after`` anyway; the engine
        decides how many reach the prompt and marks the rest.
        """
        snapshot = await self.history_snapshot(session_id, owner_id)
        return snapshot.conversation, snapshot.summary

    async def history_snapshot(self, session_id: SessionId, owner_id: UserId) -> SessionHistory:
        """:meth:`history`, with whether what it returns came from a tainted turn (KAN-101)."""
        state = await self.load(session_id, owner_id)
        return SessionHistory(
            conversation=Conversation.of(state.messages),
            summary=state.summary,
            summary_tainted=state.summary_tainted,
        )

    async def clear(self, session_id: SessionId, owner_id: UserId) -> None:
        """Forget the owner's state for this session (and any legacy un-namespaced state it
        owned). Takes the owner for the same reason :meth:`load` does: a session id alone must
        not let one user clear another's conversation.

        Under the same per-session lock as :meth:`append`, so a clear cannot interleave with an
        append's load-and-write and be undone by it."""
        key = self._key(session_id, owner_id)
        async with self._exclusive(key):
            await self._cache.delete(key)
            legacy = await self._cache.get(self._key(session_id))
            if legacy is not None:
                state = SessionState.from_json(legacy)
                if state is None or state.owner_id == owner_id:
                    await self._cache.delete(self._key(session_id))

    async def _summarise(self, overflow: Sequence[Message], previous: str) -> str:
        """Fold overflow turns into the running summary.

        Falls back to a truncated transcript when no summariser is configured. Losing the
        conversation entirely would be worse than keeping a crude record of it, and the fallback
        is clearly marked (:data:`FALLBACK_SUMMARY_MARKER`) so it is never mistaken for a
        model-written summary.
        """
        transcript = "\n".join(f"{m.role}: {m.content}" for m in overflow)
        if self._summariser is None:
            return _fallback_summary(previous, transcript)
        try:
            return await self._summariser(transcript, previous)
        except Exception:
            logger.warning("conversation summarisation failed; keeping truncated transcript")
            return _fallback_summary(previous, transcript)


#: Leads every summary that is raw transcript rather than a model's summary (KAN-101). The
#: docstring above always promised this mark; the text never carried it.
FALLBACK_SUMMARY_MARKER = "[unsummarised transcript excerpt]"
_FALLBACK_SUMMARY_MAX_CHARS = 4000


def _fallback_summary(previous: str, transcript: str) -> str:
    """The newest ``_FALLBACK_SUMMARY_MAX_CHARS`` of the transcript so far, marker first.

    The marker is stripped from ``previous`` before merging and put back once, so it survives
    the tail cut and is never repeated.
    """
    if previous.startswith(FALLBACK_SUMMARY_MARKER):
        previous = previous[len(FALLBACK_SUMMARY_MARKER):].lstrip("\n")
    merged = f"{previous}\n{transcript}" if previous else transcript
    room = _FALLBACK_SUMMARY_MAX_CHARS - len(FALLBACK_SUMMARY_MARKER) - 1
    return f"{FALLBACK_SUMMARY_MARKER}\n{merged[-room:]}"


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
