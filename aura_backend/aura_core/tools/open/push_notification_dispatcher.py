"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. ``dispatch_to_webhook``
POSTs to any caller-named URL with ``urllib`` and no SSRF guard (contrast ``fetch_url``'s
``_assert_public_url``), and without a URL ``dispatch`` reports ``status: "delivered"`` when nothing
was sent. AURA's outbound notices go through ``notifications/service.py`` (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ._net import http_url_error, open_no_redirect, public_http_url_error


class PushNotificationDispatcher:
    """Dispatcher for building and sending task, CI, and approval notifications to Webhooks, Slack, Discord, and ntfy."""

    ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}

    PRIORITY_COLORS = {
        "low": 0x808080,      # Gray
        "normal": 0x3498DB,   # Blue
        "high": 0xF39C12,     # Orange
        "urgent": 0xE74C3C,   # Red
    }

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
    def format_slack_block_kit(
        cls,
        title: str,
        message: str,
        event_type: str = "task_alert",
        priority: str = "normal",
        action_url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Format notification as a rich Slack Block Kit payload."""
        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"[{priority.upper()}] {title}", "emoji": True},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*Event:* `{event_type}`\n{message}"},
            },
        ]

        if action_url:
            blocks.append({
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Details"},
                        "url": action_url,
                        "style": "primary" if priority in ("high", "urgent") else "default",
                    }
                ],
            })

        return {"blocks": blocks}

    @classmethod
    def format_discord_embed(
        cls,
        title: str,
        message: str,
        event_type: str = "task_alert",
        priority: str = "normal",
    ) -> Dict[str, Any]:
        """Format notification as a Discord embed object with priority color encoding."""
        norm_priority = priority.lower() if priority.lower() in cls.ALLOWED_PRIORITIES else "normal"
        color = cls.PRIORITY_COLORS.get(norm_priority, 0x3498DB)

        return {
            "embeds": [
                {
                    "title": title,
                    "description": message,
                    "color": color,
                    "fields": [
                        {"name": "Priority", "value": norm_priority.upper(), "inline": True},
                        {"name": "Event Type", "value": event_type, "inline": True},
                    ],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            ]
        }

    @classmethod
    def format_ntfy_headers(
        cls,
        title: str,
        priority: str = "normal",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Generate headers for ntfy.sh push notification endpoints."""
        ntfy_priority_map = {
            "low": "2",
            "normal": "3",
            "high": "4",
            "urgent": "5",
        }
        headers = {
            "Title": title,
            "Priority": ntfy_priority_map.get(priority.lower(), "3"),
        }
        if tags:
            headers["Tags"] = ",".join(tags)
        return headers

    @classmethod
    def dispatch_to_webhook(
        cls,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 5.0,
        allow_private: bool = False,
    ) -> Dict[str, Any]:
        """Send JSON payload to target webhook URL.

        Only ``http``/``https`` URLs whose host resolves to public addresses are accepted, and a
        redirect is not followed (it could lead to an internal address). ``allow_private`` lifts
        the address check, not the scheme check, for a caller that deliberately targets a local
        service.
        """
        problem = http_url_error(url) if allow_private else public_http_url_error(url)
        if problem:
            return {"success": False, "status_code": 0, "error": problem, "url": url}
        req_headers = {"Content-Type": "application/json"}
        if headers:
            req_headers.update(headers)

        try:
            body = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
            with open_no_redirect(req, timeout_seconds) as resp:
                code = resp.getcode()
                return {
                    "success": 200 <= code < 300,
                    "status_code": code,
                    "url": url,
                }
        except urllib.error.HTTPError as exc:
            return {"success": False, "status_code": exc.code, "error": str(exc), "url": url}
        except Exception as exc:
            return {"success": False, "status_code": 0, "error": f"{type(exc).__name__}: {exc}", "url": url}

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

        res = cls.dispatch_to_webhook(webhook_url, payload, timeout_seconds=timeout_seconds)
        return {
            "dispatched": res["success"],
            "channel": "webhook",
            "status_code": res.get("status_code"),
            "status": "delivered" if res["success"] else "failed",
            "error": res.get("error"),
            "payload": payload,
        }
