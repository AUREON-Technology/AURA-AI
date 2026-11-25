"""In-memory push store and a recording sender -- the real substrate for tests and a single process.

``RecordingPushSender`` is a working sender that delivers to a list (what a test or a deployment with
push switched off for real delivery inspects); it is not a mock of Firebase.
"""

from __future__ import annotations

from typing import Sequence

from ..domain.push import MAX_DEVICES_PER_USER, PushDevice, PushMessage, PushOutcome

__all__ = ["InMemoryPushDeviceStore", "RecordingPushSender"]


class InMemoryPushDeviceStore:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], PushDevice] = {}

    async def upsert(self, device: PushDevice) -> None:
        for key in [k for k, v in self._rows.items() if v.token == device.token and k != (device.user_id, device.device_id)]:
            del self._rows[key]
        self._rows[(device.user_id, device.device_id)] = device
        mine = sorted((v for k, v in self._rows.items() if k[0] == device.user_id), key=lambda d: d.updated_at)
        for old in mine[: max(0, len(mine) - MAX_DEVICES_PER_USER)]:
            del self._rows[(old.user_id, old.device_id)]

    async def remove(self, user_id: str, device_id: str) -> bool:
        return self._rows.pop((user_id, device_id), None) is not None

    async def remove_token(self, token: str) -> int:
        gone = [k for k, v in self._rows.items() if v.token == token]
        for key in gone:
            del self._rows[key]
        return len(gone)

    async def for_user(self, user_id: str) -> Sequence[PushDevice]:
        return sorted((v for k, v in self._rows.items() if k[0] == user_id), key=lambda d: d.updated_at, reverse=True)


class RecordingPushSender:
    def __init__(self, *, dead_tokens: Sequence[str] = ()) -> None:
        self.sent: list[tuple[PushDevice, PushMessage]] = []
        self._dead = set(dead_tokens)

    async def send(self, device: PushDevice, message: PushMessage) -> PushOutcome:
        if device.token in self._dead:
            return PushOutcome("invalid_token", "recorded as dead")
        self.sent.append((device, message))
        return PushOutcome("delivered")
