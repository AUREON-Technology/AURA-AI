"""Redis implementation of :class:`~aura_core.adapters.host_agent_relay.RelayBus`.

Pub/sub carries relayed host-agent calls and their frames between gateway workers; plain keys
with a TTL carry "which worker holds this user's desktop socket".

**Two clients, deliberately.** Commands (``PUBLISH``, the ownership keys) use a short socket
timeout, like :class:`~aura_core.adapters.redis_cache.RedisCache`: they sit in a request path and
a slow answer is worse than a fast failure. A subscription is the opposite, an indefinitely idle
read. Sharing the command client's 2 s timeout would end every quiet subscription with a timeout
error, so the subscription connection has no read timeout and relies on Redis health checks
instead.

Both clients are built lazily on first use (the import too), so constructing the container never
opens a socket and a deployment without Redis never needs the package.

Ownership changes are compare-and-set Lua scripts, so a heartbeat can never overwrite a newer
owner and a disconnect can never delete one.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from ..domain.errors import ConfigurationError

__all__ = ["RedisRelayBus"]

# Extend the key if it still holds our value, or set it if it is gone; refuse if someone else
# holds it. Returns 1 on success, 0 when another worker owns the key.
_REFRESH = """
local current = redis.call('GET', KEYS[1])
if current == false or current == ARGV[1] then
  redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
  return 1
end
return 0
"""

# Delete the key only if it still holds our value.
_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
  return redis.call('DEL', KEYS[1])
end
return 0
"""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


class _RedisSubscription:
    def __init__(self, pubsub: Any) -> None:
        self._pubsub = pubsub

    def __aiter__(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        while True:
            # A bounded poll rather than listen(): cancelling a task blocked here is prompt, and
            # a dead connection surfaces as an exception the relay's listener retries on.
            message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message is None:
                continue
            if message.get("type") == "message":
                data = message.get("data")
                yield data if isinstance(data, bytes) else str(data).encode("utf-8")

    async def close(self) -> None:
        try:
            await self._pubsub.unsubscribe()
        finally:
            await self._pubsub.aclose()


class RedisRelayBus:
    def __init__(self, url: str, *, socket_timeout_s: float = 2.0) -> None:
        if not url:
            raise ConfigurationError(
                "the host-agent relay requires AURA_REDIS_URL",
                details={"setting": "AURA_REDIS_URL"},
            )
        self._url = url
        self._socket_timeout = socket_timeout_s
        self._commands: Any = None
        self._subscriber: Any = None

    def _redis(self) -> Any:
        try:
            import redis.asyncio as redis
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise ConfigurationError(
                "the host-agent relay requires the redis package",
                details={"install": "pip install redis"},
            ) from exc
        return redis

    def _command_client(self) -> Any:
        if self._commands is None:
            self._commands = self._redis().from_url(
                self._url,
                socket_timeout=self._socket_timeout,
                socket_connect_timeout=self._socket_timeout,
                decode_responses=False,
            )
        return self._commands

    def _subscriber_client(self) -> Any:
        if self._subscriber is None:
            self._subscriber = self._redis().from_url(
                self._url,
                socket_timeout=None,
                socket_connect_timeout=self._socket_timeout,
                health_check_interval=30,
                decode_responses=False,
            )
        return self._subscriber

    async def publish(self, channel: str, message: bytes) -> None:
        await self._command_client().publish(channel, message)

    async def subscribe(self, channel: str) -> _RedisSubscription:
        pubsub = self._subscriber_client().pubsub()
        await pubsub.subscribe(channel)
        return _RedisSubscription(pubsub)

    async def claim(self, key: str, value: str, ttl_s: float) -> str | None:
        previous = await self._command_client().set(key, value, px=int(ttl_s * 1000), get=True)
        return _text(previous)

    async def refresh(self, key: str, value: str, ttl_s: float) -> bool:
        result = await self._command_client().eval(_REFRESH, 1, key, value, int(ttl_s * 1000))
        return bool(result)

    async def release(self, key: str, value: str) -> None:
        await self._command_client().eval(_RELEASE, 1, key, value)

    async def owner(self, key: str) -> str | None:
        return _text(await self._command_client().get(key))

    async def close(self) -> None:
        for client in (self._commands, self._subscriber):
            if client is not None:
                await client.aclose()
        self._commands = None
        self._subscriber = None
