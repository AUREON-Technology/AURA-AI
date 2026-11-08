"""Cross-worker relay for desktop host-agent sessions.

The problem this solves (ADR-011, "Deployment constraint"): a paired ``aura_hostd`` daemon holds
one long-lived WebSocket, and that socket is terminated by exactly one gateway worker process.
:class:`~aura_core.adapters.host_agent.HostAgentRegistry` is per process, so with
``--workers 4`` a chat turn served by any of the other three workers found no session and told
the user no desktop was connected, while the desktop app showed itself as connected.

The relay lets any worker reach the session wherever it lives:

* The worker holding the socket **owns** it: a short-lived record ``owner:<user>`` names that
  worker, refreshed by a heartbeat and dropped on disconnect. The newest connection always takes
  ownership, the same "a second pairing replaces the first" rule the registry already applies
  in-process; the previous owner is told its session was superseded.
* A worker that does not own the session gets a :class:`RemoteHostAgentSession` with the same
  ``call``/``call_streaming`` surface as a local
  :class:`~aura_core.adapters.host_agent.HostAgentSession`. Each call is published to the owner's
  channel; the owner runs it against its local session and publishes every frame back (``stream``
  increments as they arrive, then the terminal ``result`` or ``error``). The tool layer cannot
  tell the difference, which is the point: nothing above the registry changes.

**Signed messages.** The relay carries commands for a user's own machine. Without a signature,
anyone able to write to the shared bus could ask any paired desktop to run something; with one,
bus access is no more powerful than it was before this module existed. Every message is
HMAC-SHA256 signed with a key derived from the deployment's JWT secret (which every gateway
worker already shares, or tokens one worker minted would not verify on another), carries a
timestamp checked against a short window, and a call id that is refused if seen again. The
daemon's own containment and consent tiers still apply on top of this, as they always have -- the
relay is a transport, not a policy.

**Failure policy.** Bus errors never make a local session unreachable: the registry falls back to
local-only resolution and logs. A remote call whose owner never answers fails with the same
timeout a silent local daemon produces.

The transport is a :class:`RelayBus`. :class:`InMemoryRelayBus` lets several registries in one
process act as separate workers (the default test suite; no Redis needed);
``adapters/redis_relay_bus.py`` is the production implementation.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol

from ..domain.identifiers import UserId
from ..observability.logging import get_logger
from .docker_sandbox import SandboxError

__all__ = [
    "RelayBus",
    "RelaySubscription",
    "InMemoryRelayBus",
    "HostAgentRelay",
    "RemoteHostAgentSession",
]

logger = get_logger("adapters.host_agent_relay")

_PREFIX = "aura:host-agent"

#: How long an ownership record lives without a heartbeat. Short enough that a worker that died
#: without unregistering stops attracting calls quickly; the heartbeat runs at a third of this.
DEFAULT_OWNER_TTL_S = 30.0

#: How far a message's timestamp may be from this worker's clock. Workers of one deployment share
#: a host or a region; this bounds replay without being tight enough for clock skew to matter.
_MAX_MESSAGE_AGE_S = 30.0

#: Extra wait a caller allows past the call's own timeout, so the owner's own (more specific)
#: timeout error arrives before the caller gives up with a generic one.
_REPLY_SLACK_S = 2.0

_KEY_LABEL = b"aura-host-agent-relay/v1"

#: How long a caller waits for this worker's own subscription before giving up. The listener
#: keeps retrying behind it; this only bounds how long one request blocks on a bus outage.
_SUBSCRIBE_TIMEOUT_S = 5.0


class RelaySubscription(Protocol):
    """A live subscription to one channel. Iterating yields each message's bytes."""

    def __aiter__(self) -> AsyncIterator[bytes]: ...

    async def close(self) -> None: ...


class RelayBus(Protocol):
    """Pub/sub plus a compare-and-set ownership record -- everything the relay needs from a
    shared store, and nothing more."""

    async def publish(self, channel: str, message: bytes) -> None: ...

    async def subscribe(self, channel: str) -> RelaySubscription:
        """Returns once the subscription is active, so a message published after this returns
        is guaranteed to be delivered to it."""
        ...

    async def claim(self, key: str, value: str, ttl_s: float) -> str | None:
        """Set ``key`` to ``value`` unconditionally; return the value it replaced, if any."""
        ...

    async def refresh(self, key: str, value: str, ttl_s: float) -> bool:
        """Extend ``key`` if it still holds ``value`` (or set it if absent). ``False`` means
        another value now holds it."""
        ...

    async def release(self, key: str, value: str) -> None:
        """Delete ``key`` only if it still holds ``value``."""
        ...

    async def owner(self, key: str) -> str | None: ...

    async def close(self) -> None: ...


# -- in-memory transport ---------------------------------------------------------------------


class _InMemorySubscription:
    def __init__(self, bus: "InMemoryRelayBus", channel: str) -> None:
        self._bus = bus
        self._channel = channel
        self._queue: "asyncio.Queue[bytes | None]" = asyncio.Queue()
        self._closed = False

    def deliver(self, message: bytes) -> None:
        if not self._closed:
            self._queue.put_nowait(message)

    async def __anext__(self) -> bytes:
        item = await self._queue.get()
        if item is None:
            raise StopAsyncIteration
        return item

    def __aiter__(self) -> "_InMemorySubscription":
        return self

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unsubscribe(self._channel, self)
            self._queue.put_nowait(None)


class InMemoryRelayBus:
    """A :class:`RelayBus` inside one process. Several :class:`HostAgentRelay` instances sharing
    one of these behave exactly like separate workers sharing Redis, which is what the default
    test suite needs; a single-process deployment has no use for it."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._subscribers: dict[str, list[_InMemorySubscription]] = {}
        self._keys: dict[str, tuple[str, float]] = {}

    async def publish(self, channel: str, message: bytes) -> None:
        for subscription in list(self._subscribers.get(channel, ())):
            subscription.deliver(message)

    async def subscribe(self, channel: str) -> RelaySubscription:
        subscription = _InMemorySubscription(self, channel)
        self._subscribers.setdefault(channel, []).append(subscription)
        return subscription

    def _unsubscribe(self, channel: str, subscription: _InMemorySubscription) -> None:
        subscribers = self._subscribers.get(channel, [])
        if subscription in subscribers:
            subscribers.remove(subscription)

    def _live(self, key: str) -> str | None:
        entry = self._keys.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if self._clock() >= expires_at:
            del self._keys[key]
            return None
        return value

    async def claim(self, key: str, value: str, ttl_s: float) -> str | None:
        previous = self._live(key)
        self._keys[key] = (value, self._clock() + ttl_s)
        return previous

    async def refresh(self, key: str, value: str, ttl_s: float) -> bool:
        current = self._live(key)
        if current is not None and current != value:
            return False
        self._keys[key] = (value, self._clock() + ttl_s)
        return True

    async def release(self, key: str, value: str) -> None:
        if self._live(key) == value:
            del self._keys[key]

    async def owner(self, key: str) -> str | None:
        return self._live(key)

    async def close(self) -> None:
        return None


# -- the relay -------------------------------------------------------------------------------


class RemoteHostAgentSession:
    """A host-agent session that lives on another worker, reached through the relay.

    Same ``user_id``/``call``/``call_streaming`` surface as
    :class:`~aura_core.adapters.host_agent.HostAgentSession`, and the same timeout semantics: a
    single-shot ``call`` has an overall deadline, a streaming call an idle timeout between frames.
    """

    def __init__(self, relay: "HostAgentRelay", user_id: UserId, owner: str) -> None:
        self.user_id = user_id
        self.owner = owner
        self._relay = relay

    async def call(self, method: str, params: Mapping[str, Any], *, timeout_s: float) -> Any:
        async with self._relay._remote_call(
            self.owner, self.user_id, method, params, timeout_s=timeout_s, stream=False
        ) as replies:
            deadline = asyncio.get_running_loop().time() + timeout_s + _REPLY_SLACK_S
            while True:
                remaining = deadline - asyncio.get_running_loop().time()
                frame = await _next_reply(replies, method, max(remaining, 0.0), timeout_s)
                ftype = frame.get("type")
                if ftype == "error":
                    raise SandboxError(str(frame.get("message") or "host agent error"))
                if ftype == "stream":
                    # A single-shot call waits for the terminal result, exactly as
                    # HostAgentSession.resolve does locally.
                    continue
                return frame.get("result")

    async def call_streaming(
        self, method: str, params: Mapping[str, Any], *, timeout_s: float
    ) -> AsyncIterator[Mapping[str, Any]]:
        async with self._relay._remote_call(
            self.owner, self.user_id, method, params, timeout_s=timeout_s, stream=True
        ) as replies:
            while True:
                frame = await _next_reply(replies, method, timeout_s + _REPLY_SLACK_S, timeout_s)
                ftype = frame.get("type")
                if ftype == "error":
                    raise SandboxError(str(frame.get("message") or "host agent error"))
                yield frame
                if ftype != "stream":
                    return


async def _next_reply(
    replies: "asyncio.Queue[Mapping[str, Any]]", method: str, wait_s: float, timeout_s: float
) -> Mapping[str, Any]:
    try:
        return await asyncio.wait_for(replies.get(), timeout=wait_s)
    except asyncio.TimeoutError as exc:
        raise SandboxError(
            f"desktop app did not respond to {method!r} within {timeout_s:.0f}s"
        ) from exc


class _CallContext:
    """Registers a reply queue for one call id, publishes the call, and always unregisters."""

    def __init__(self, relay: "HostAgentRelay", owner: str, message: dict[str, Any]) -> None:
        self._relay = relay
        self._owner = owner
        self._message = message
        self._queue: "asyncio.Queue[Mapping[str, Any]]" = asyncio.Queue()

    async def __aenter__(self) -> "asyncio.Queue[Mapping[str, Any]]":
        await self._relay._ensure_listening()
        call_id = self._message["call_id"]
        self._relay._waiters[call_id] = self._queue
        try:
            await self._relay._publish(self._owner, self._message)
        except Exception as exc:
            self._relay._waiters.pop(call_id, None)
            raise SandboxError(
                "could not reach the server worker holding the desktop connection"
            ) from exc
        return self._queue

    async def __aexit__(self, *exc_info: Any) -> None:
        self._relay._waiters.pop(self._message["call_id"], None)


class HostAgentRelay:
    """One worker's end of the relay. Owned by a
    :class:`~aura_core.adapters.host_agent.HostAgentRegistry`, which supplies the two callbacks
    the relay needs into it: find a local session, and drop one that was superseded elsewhere.

    Nothing connects until first use (the listener starts on the first ``claim`` or remote call),
    matching every other adapter's lazy-construction rule.
    """

    def __init__(
        self,
        bus: RelayBus,
        secret: str,
        *,
        worker_id: str | None = None,
        owner_ttl_s: float = DEFAULT_OWNER_TTL_S,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not secret:
            raise ValueError("the host-agent relay needs a signing secret")
        self._bus = bus
        self._key = hmac.new(secret.encode("utf-8"), _KEY_LABEL, hashlib.sha256).digest()
        self.worker_id = worker_id or uuid.uuid4().hex
        self._owner_ttl_s = owner_ttl_s
        self._clock = clock
        self._waiters: dict[str, "asyncio.Queue[Mapping[str, Any]]"] = {}
        self._seen_calls: dict[str, float] = {}
        self._serving: set["asyncio.Task[None]"] = set()
        self._owned: set[str] = set()
        self._listener: "asyncio.Task[None] | None" = None
        self._heartbeat: "asyncio.Task[None] | None" = None
        self._subscribed = asyncio.Event()
        self._start_lock = asyncio.Lock()
        self._closed = False
        self._local_session: Callable[[UserId], Awaitable[Any]] | None = None
        self._superseded: Callable[[UserId], Awaitable[None]] | None = None

    def bind(
        self,
        *,
        local_session: Callable[[UserId], Awaitable[Any]],
        superseded: Callable[[UserId], Awaitable[None]],
    ) -> None:
        self._local_session = local_session
        self._superseded = superseded

    # -- ownership ----------------------------------------------------------------------------

    @staticmethod
    def _owner_key(user_id: UserId) -> str:
        return f"{_PREFIX}:owner:{user_id.value}"

    @staticmethod
    def _channel(worker_id: str) -> str:
        return f"{_PREFIX}:worker:{worker_id}"

    async def claim(self, user_id: UserId) -> None:
        """Take ownership of ``user_id``'s session for this worker; tell a previous owner on
        another worker that its session was replaced."""
        await self._ensure_listening()
        previous = await self._bus.claim(self._owner_key(user_id), self.worker_id, self._owner_ttl_s)
        self._owned.add(user_id.value)
        if previous is not None and previous != self.worker_id:
            try:
                await self._publish(previous, {"op": "superseded", "user_id": user_id.value})
            except Exception:
                # The previous owner also learns from its own heartbeat (refresh fails once the
                # record names someone else), so a lost notice only delays it.
                logger.warning("could not notify the previous host-agent owner", exc_info=True)

    async def release(self, user_id: UserId) -> None:
        self._owned.discard(user_id.value)
        try:
            await self._bus.release(self._owner_key(user_id), self.worker_id)
        except Exception:
            # The record expires on its own within the TTL.
            logger.warning("could not release host-agent ownership", exc_info=True)

    async def owner(self, user_id: UserId) -> str | None:
        return await self._bus.owner(self._owner_key(user_id))

    def remote_session(self, user_id: UserId, owner: str) -> RemoteHostAgentSession:
        return RemoteHostAgentSession(self, user_id, owner)

    # -- messages -----------------------------------------------------------------------------

    def _sign(self, body: str) -> str:
        return hmac.new(self._key, body.encode("utf-8"), hashlib.sha256).hexdigest()

    async def _publish(self, worker_id: str, message: Mapping[str, Any]) -> None:
        body = json.dumps({**message, "from": self.worker_id, "ts": self._clock()}, separators=(",", ":"))
        envelope = json.dumps({"v": 1, "body": body, "sig": self._sign(body)}, separators=(",", ":"))
        await self._bus.publish(self._channel(worker_id), envelope.encode("utf-8"))

    def _open(self, raw: bytes) -> dict[str, Any] | None:
        """Verify and decode one envelope; ``None`` for anything forged, stale or malformed."""
        try:
            envelope = json.loads(raw)
            body = envelope["body"]
            signature = envelope["sig"]
        except (ValueError, TypeError, KeyError):
            return None
        if not isinstance(body, str) or not isinstance(signature, str):
            return None
        if not hmac.compare_digest(self._sign(body), signature):
            logger.warning("host-agent relay message with a bad signature dropped")
            return None
        try:
            message = json.loads(body)
        except ValueError:
            return None
        if not isinstance(message, dict):
            return None
        ts = message.get("ts")
        if not isinstance(ts, (int, float)) or abs(self._clock() - ts) > _MAX_MESSAGE_AGE_S:
            logger.warning("stale host-agent relay message dropped")
            return None
        return message

    def _first_sighting(self, call_id: str) -> bool:
        now = self._clock()
        cutoff = now - 2 * _MAX_MESSAGE_AGE_S
        for seen_id, seen_at in list(self._seen_calls.items()):
            if seen_at < cutoff:
                del self._seen_calls[seen_id]
        if call_id in self._seen_calls:
            return False
        self._seen_calls[call_id] = now
        return True

    def _remote_call(
        self,
        owner: str,
        user_id: UserId,
        method: str,
        params: Mapping[str, Any],
        *,
        timeout_s: float,
        stream: bool,
    ) -> _CallContext:
        message = {
            "op": "call",
            "call_id": uuid.uuid4().hex,
            "user_id": user_id.value,
            "method": method,
            "params": dict(params),
            "timeout_s": timeout_s,
            "stream": stream,
        }
        return _CallContext(self, owner, message)

    # -- listening ----------------------------------------------------------------------------

    async def _ensure_listening(self) -> None:
        if self._listener is not None and self._subscribed.is_set():
            return
        async with self._start_lock:
            if self._closed:
                raise SandboxError("the host-agent relay is shut down")
            if self._listener is None:
                self._listener = asyncio.create_task(self._listen())
                self._heartbeat = asyncio.create_task(self._beat())
        try:
            await asyncio.wait_for(self._subscribed.wait(), timeout=_SUBSCRIBE_TIMEOUT_S)
        except asyncio.TimeoutError as exc:
            # The listener keeps retrying in the background; this caller just cannot wait.
            raise SandboxError("the host-agent relay could not reach its message bus") from exc

    async def _listen(self) -> None:
        backoff = 0.5
        while not self._closed:
            subscription: RelaySubscription | None = None
            try:
                subscription = await self._bus.subscribe(self._channel(self.worker_id))
                self._subscribed.set()
                backoff = 0.5
                async for raw in subscription:
                    self._dispatch(raw)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("host-agent relay subscription failed; retrying", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 10.0)
            finally:
                if subscription is not None:
                    try:
                        await subscription.close()
                    except Exception:
                        pass

    def _dispatch(self, raw: bytes) -> None:
        message = self._open(raw)
        if message is None:
            return
        op = message.get("op")
        if op == "frame":
            queue = self._waiters.get(str(message.get("call_id") or ""))
            frame = message.get("frame")
            if queue is not None and isinstance(frame, dict):
                queue.put_nowait(frame)
        elif op == "call":
            call_id = str(message.get("call_id") or "")
            if not call_id or not self._first_sighting(call_id):
                logger.warning("replayed host-agent relay call dropped")
                return
            task = asyncio.create_task(self._serve(message))
            self._serving.add(task)
            task.add_done_callback(self._serving.discard)
        elif op == "superseded":
            user = str(message.get("user_id") or "")
            if user and self._superseded is not None:
                self._owned.discard(user)
                task = asyncio.create_task(self._superseded(UserId(user)))
                self._serving.add(task)
                task.add_done_callback(self._serving.discard)

    async def _serve(self, message: Mapping[str, Any]) -> None:
        """Run one relayed call against this worker's local session and publish its frames back."""
        reply_to = str(message.get("from") or "")
        call_id = str(message.get("call_id") or "")

        async def reply(frame: Mapping[str, Any]) -> None:
            await self._publish(reply_to, {"op": "frame", "call_id": call_id, "frame": dict(frame)})

        session = None
        if self._local_session is not None:
            session = await self._local_session(UserId(str(message.get("user_id") or "")))
        if session is None:
            await reply({"type": "error", "message": "the desktop app disconnected -- reconnect and try again"})
            return
        method = str(message.get("method") or "")
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        timeout_s = float(message.get("timeout_s") or 60.0)
        try:
            if message.get("stream"):
                async for frame in session.call_streaming(method, params, timeout_s=timeout_s):
                    await reply(frame)
            else:
                result = await session.call(method, params, timeout_s=timeout_s)
                await reply({"type": "result", "result": result})
        except SandboxError as exc:
            await reply({"type": "error", "message": str(exc)})
        except Exception:
            logger.exception("relayed host-agent call failed")
            await reply({"type": "error", "message": "host agent error"})

    async def _beat(self) -> None:
        interval = self._owner_ttl_s / 3
        while not self._closed:
            await asyncio.sleep(interval)
            for user in list(self._owned):
                try:
                    still_ours = await self._bus.refresh(
                        self._owner_key(UserId(user)), self.worker_id, self._owner_ttl_s
                    )
                except Exception:
                    logger.warning("host-agent ownership heartbeat failed", exc_info=True)
                    continue
                if not still_ours:
                    # Another worker took the session without our hearing the notice.
                    self._owned.discard(user)
                    if self._superseded is not None:
                        await self._superseded(UserId(user))

    async def close(self) -> None:
        """Stop listening, release what this worker owns, and close the bus. Idempotent."""
        if self._closed:
            return
        self._closed = True
        tasks = [t for t in (self._listener, self._heartbeat) if t is not None] + list(self._serving)
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        for user in list(self._owned):
            await self.release(UserId(user))
        await self._bus.close()
