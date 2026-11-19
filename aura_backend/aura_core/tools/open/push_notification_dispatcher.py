import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


class PushNotificationDispatcher:
    """Dispatcher for building and optionally sending task/approval notifications via webhook."""

    ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}

    @classmethod
    def build_payload(
        cls,
        title: str,
        message: str,
        event_type: str = "task_completed",
        priority: str = "normal",
    ) -> Dict[str, Any]:
        """Construct a normalized notification payload."""
        norm_priority = priority.lower() if priority.lower() in cls.ALLOWED_PRIORITIES else "normal"
        return {
            "title": title.strip(),
            "message": message.strip(),
            "event_type": event_type.strip(),
            "priority": norm_priority,
            "timestamp": time.time(),
        }

    @classmethod
    def dispatch(
        cls,
        title: str,
        message: str,
        event_type: str = "task_completed",
        priority: str = "normal",
        webhook_url: Optional[str] = None,
        timeout_seconds: float = 5.0,
    ) -> Dict[str, Any]:
        """Dispatch a notification locally or via an HTTP POST webhook if webhook_url is given."""
        payload = cls.build_payload(
            title=title,
            message=message,
            event_type=event_type,
            priority=priority,
        )

        if not webhook_url:
            return {
                "dispatched": True,
                "channel": "local_notification_center",
                "status": "delivered",
                "payload": payload,
            }

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                webhook_url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                code = resp.getcode()
                return {
                    "dispatched": 200 <= code < 300,
                    "channel": "webhook",
                    "status_code": code,
                    "status": "delivered" if 200 <= code < 300 else "failed",
                    "payload": payload,
                }
        except urllib.error.HTTPError as exc:
            return {
                "dispatched": False,
                "channel": "webhook",
                "status_code": exc.code,
                "status": "failed",
                "error": str(exc),
                "payload": payload,
            }
        except Exception as exc:
            return {
                "dispatched": False,
                "channel": "webhook",
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
                "payload": payload,
            }