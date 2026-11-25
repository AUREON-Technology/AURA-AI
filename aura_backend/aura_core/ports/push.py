"""Ports for push notifications (``domain/push.py``, ADR-060)."""

from __future__ import annotations

from typing import Protocol, Sequence, runtime_checkable

from ..domain.push import PushDevice, PushMessage, PushOutcome

__all__ = ["PushDeviceStore", "PushSender"]


@runtime_checkable
class PushDeviceStore(Protocol):
    async def upsert(self, device: PushDevice) -> None:
        """Register or refresh ``(user_id, device_id)``. The same token under another device or user is
        moved here: a token belongs to one install, so a shared phone never notifies the old account."""

    async def remove(self, user_id: str, device_id: str) -> bool:
        """Forget one device of ``user_id``; whether it existed."""

    async def remove_token(self, token: str) -> int:
        """Forget every registration of a dead token."""

    async def for_user(self, user_id: str) -> Sequence[PushDevice]:
        """The user's devices, newest first."""


@runtime_checkable
class PushSender(Protocol):
    async def send(self, device: PushDevice, message: PushMessage) -> PushOutcome:
        """Deliver one message. Never raises for a delivery problem: it returns the outcome."""
