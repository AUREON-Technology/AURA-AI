"""Push notifications (ADR-060): the device a person registered and the message sent to it.

stdlib only. A push is a *nudge to open the app*, never the content: what the person is asked to
approve is shown by the app after it fetches it over the authenticated API. The default message
therefore carries a generic title and body and the ids needed to open the right screen -- not tool
arguments, file paths or commands, which would sit on a lock screen and in Google's pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

__all__ = ["PushDevice", "PushMessage", "PushOutcome", "PLATFORMS", "MAX_DEVICES_PER_USER", "MAX_TOKEN_CHARS"]

PLATFORMS = ("android", "ios", "web", "macos", "windows", "linux")
MAX_DEVICES_PER_USER = 10
MAX_TOKEN_CHARS = 4096


@dataclass(frozen=True, slots=True)
class PushDevice:
    user_id: str
    device_id: str
    token: str
    platform: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PushMessage:
    title: str
    body: str
    #: Strings only (FCM data payloads are string maps). Ids and a ``type``, never content.
    data: Mapping[str, str] = field(default_factory=dict)
    #: Same ``collapse_key`` replaces an earlier undelivered push of the same kind.
    collapse_key: str = ""


@dataclass(frozen=True, slots=True)
class PushOutcome:
    """``delivered`` -- accepted by the push service. ``invalid_token`` -- the service said the token
    is dead (app uninstalled / token rotated): the notifier forgets it. ``error`` -- anything else,
    kept so it can be counted; the token is kept too."""

    status: str  # "delivered" | "invalid_token" | "error"
    detail: str = ""
