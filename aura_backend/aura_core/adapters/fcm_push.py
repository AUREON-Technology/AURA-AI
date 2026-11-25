"""Firebase Cloud Messaging :class:`~aura_core.ports.push.PushSender` (ADR-060).

``firebase_admin`` is imported lazily, inside the call, like every vendor SDK here, and the app is
initialised on first send from a service-account file named by ``AURA_PUSH_FCM_CREDENTIALS_FILE``
(a path; the key itself is never a setting). The blocking SDK call runs in a thread.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

from ..domain.push import PushDevice, PushMessage, PushOutcome

__all__ = ["FcmPushSender"]

# INVALID_ARGUMENT is not here on purpose: it also means a malformed message, and treating that as a dead
# token would delete working registrations on a payload bug.
_DEAD = ("UNREGISTERED", "SENDER_ID_MISMATCH")


class FcmPushSender:
    def __init__(self, credentials_file: str, *, app_name: str = "aura-push") -> None:
        self._file = credentials_file
        self._name = app_name
        self._app: Any = None
        self._lock = threading.Lock()

    def _get_app(self) -> Any:
        with self._lock:
            if self._app is None:
                import firebase_admin
                from firebase_admin import credentials

                try:
                    self._app = firebase_admin.get_app(self._name)
                except ValueError:
                    self._app = firebase_admin.initialize_app(credentials.Certificate(self._file), name=self._name)
            return self._app

    def _send_blocking(self, device: PushDevice, message: PushMessage) -> PushOutcome:
        try:
            from firebase_admin import messaging

            app = self._get_app()
            note = messaging.Message(
                token=device.token,
                notification=messaging.Notification(title=message.title, body=message.body),
                data={str(k): str(v) for k, v in message.data.items()},
                android=messaging.AndroidConfig(
                    priority="high", collapse_key=message.collapse_key or None),
                apns=messaging.APNSConfig(headers={"apns-collapse-id": message.collapse_key}
                                          if message.collapse_key else {}),
            )
            messaging.send(note, app=app)
            return PushOutcome("delivered")
        except Exception as exc:  # the SDK raises its own types; classify by name/code, never leak a token
            code = str(getattr(exc, "code", "") or "").upper()
            name = type(exc).__name__.upper()
            if any(d in code or d in name for d in _DEAD):
                return PushOutcome("invalid_token", type(exc).__name__)
            return PushOutcome("error", type(exc).__name__)

    async def send(self, device: PushDevice, message: PushMessage) -> PushOutcome:
        return await asyncio.to_thread(self._send_blocking, device, message)
