"""Not exposed (ADR-036, 2026-11-21) -- deliberately NOT registered as a tool. ``dispatch_to_webhook``
POSTs to any caller-named URL with ``urllib`` and no SSRF guard (contrast ``fetch_url``'s
``_assert_public_url``), and without a URL ``dispatch`` reports ``status: "delivered"`` when nothing
was sent. AURA's outbound notices go through ``notifications/service.py`` (KAN-34).

Reachable from no persona: nothing under ``aura_core`` imports this module, which
``tests/architecture/test_open_tools_classification.py`` checks against
``tools/open/_classification.py``. Kept as reference code; change that classification and
ADR-036 before wiring it in.
"""

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ._ops_common import UrlRefused, open_guarded, redact_url

_MAX_TITLE = 256
_MAX_MESSAGE = 4000
_MAX_EVENT = 100
_MAX_PAYLOAD_BYTES = 256 * 1024


def _text(value: Any, limit: int) -> str:
    """``value`` as a stripped string of at most ``limit`` characters; None becomes ''."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    return text.strip()[:limit]


def _slack_escape(text: str) -> str:
    """Slack's required escaping: without it ``<!channel>`` in a message pings the whole channel."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _http_url_or_none(url: Any) -> Optional[str]:
    """``url`` if it is a plain http(s) link (no ``javascript:`` and the like), else None."""
    if not isinstance(url, str) or not url.strip() or len(url) > 2000:
        return None
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme in ("http", "https") and parts.hostname else None


def _header_safe(value: str) -> str:
    """A header value that cannot inject a second header and survives non-Latin-1 text.

    HTTP header text is Latin-1 on the wire. Anything else (Arabic, emoji) is sent as an RFC 2047
    encoded word, which ntfy decodes; unencoded it makes ``http.client`` raise.
    """
    cleaned = "".join(ch for ch in value if ch not in "\r\n\x00").strip()
    try:
        cleaned.encode("latin-1")
        return cleaned
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(cleaned.encode("utf-8")).decode("ascii") + "?="


class PushNotificationDispatcher:
    """Dispatcher for building and sending task, CI, and approval notifications to Webhooks, Slack, Discord, and ntfy.

    Safety properties:

    * a webhook is only called over http/https on a **public** host -- no opt-out: loopback, private,
      internal names, the platform's own hosts (so never the AURA gateway) and ``/api/v3/`` or
      ``/health`` paths are refused, as is ``file://`` -- and a redirect is **not followed** (a POST that quietly became a GET elsewhere is a failure);
    * webhook URLs embed their secret in the path (``hooks.slack.com/services/T0/B0/<secret>``), so
      results show ``scheme://host/...`` only;
    * ``dispatch`` without a ``webhook_url`` no longer reports ``delivered``: there is no local
      notification centre behind it, so it says ``not_sent``;
    * Slack text is escaped (``<!channel>`` cannot ping a channel) and Discord mentions are disabled.

    Reads no files, so there is no PDF handling to do.
    """

    ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}

    PRIORITY_COLORS = {
        "low": 0x808080,      # Gray
        "normal": 0x3498DB,   # Blue
        "high": 0xF39C12,     # Orange
        "urgent": 0xE74C3C,   # Red
    }

    @classmethod
    def _norm_priority(cls, priority: Any) -> str:
        if isinstance(priority, str) and priority.strip().lower() in cls.ALLOWED_PRIORITIES:
            return priority.strip().lower()
        return "normal"

    @classmethod
    def build_payload(
        cls,
        title: str,
        message: str,
        event_type: str = "task_completed",
        priority: str = "normal",
    ) -> Dict[str, Any]:
        """Construct a normalized notification payload."""
        return {
            "title": _text(title, _MAX_TITLE),
            "message": _text(message, _MAX_MESSAGE),
            "event_type": _text(event_type, _MAX_EVENT),
            "priority": cls._norm_priority(priority),
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
        """Format notification as a rich Slack Block Kit payload.

        ``action_url`` becomes a button only when it is an http(s) link; anything else is omitted.
        """
        norm = cls._norm_priority(priority)
        header = f"[{norm.upper()}] {_text(title, _MAX_TITLE)}"[:150]
        event = _text(event_type, _MAX_EVENT).replace("`", "'")
        body = f"*Event:* `{_slack_escape(event)}`\n{_slack_escape(_text(message, _MAX_MESSAGE))}"
        blocks: List[Dict[str, Any]] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": header, "emoji": True},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": body[:3000]},
            },
        ]

        link = _http_url_or_none(action_url)
        if link:
            button: Dict[str, Any] = {
                "type": "button",
                "text": {"type": "plain_text", "text": "View Details"},
                "url": link,
            }
            # Slack accepts only "primary" or "danger"; the old "default" is not a valid style and
            # can get the whole message rejected.
            if norm in ("high", "urgent"):
                button["style"] = "primary"
            blocks.append({"type": "actions", "elements": [button]})

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
        norm_priority = cls._norm_priority(priority)
        color = cls.PRIORITY_COLORS.get(norm_priority, 0x3498DB)

        return {
            # No @everyone / @here / role pings from text the caller did not write.
            "allowed_mentions": {"parse": []},
            "embeds": [
                {
                    "title": _text(title, _MAX_TITLE),
                    "description": _text(message, 4000),
                    "color": color,
                    "fields": [
                        {"name": "Priority", "value": norm_priority.upper(), "inline": True},
                        {"name": "Event Type", "value": _text(event_type, 1000) or "-", "inline": True},
                    ],
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
            ],
        }

    @classmethod
    def format_ntfy_headers(
        cls,
        title: str,
        priority: str = "normal",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, str]:
        """Generate headers for ntfy.sh push notification endpoints.

        Safe for any title: line breaks are removed and non-Latin-1 text (Arabic, emoji) is sent as an
        RFC 2047 encoded word instead of failing the request.
        """
        ntfy_priority_map = {
            "low": "2",
            "normal": "3",
            "high": "4",
            "urgent": "5",
        }
        headers = {
            "Title": _header_safe(_text(title, 250)),
            "Priority": ntfy_priority_map[cls._norm_priority(priority)],
        }
        if isinstance(tags, (list, tuple)):
            clean = [t.replace(",", " ").strip() for t in (str(x) for x in tags) if t.strip()]
            if clean:
                headers["Tags"] = _header_safe(",".join(clean))[:500]
        return headers

    @classmethod
    def dispatch_to_webhook(
        cls,
        url: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        timeout_seconds: float = 5.0,
    ) -> Dict[str, Any]:
        """Send JSON payload to target webhook URL. Every failure is a returned result.

        The URL must pass the outbound policy in ``_ops_common`` (public host, no redirects); there
        is no switch to relax it.
        """
        shown = redact_url(url)

        def failed(error: str, status: int = 0) -> Dict[str, Any]:
            if isinstance(url, str) and url:
                error = error.replace(url, shown)
            return {"success": False, "status_code": status, "error": error, "url": shown}

        req_headers = {"Content-Type": "application/json"}
        if headers:
            if not isinstance(headers, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
            ):
                return failed("headers must be a mapping of strings to strings.")
            if any(("\r" in k or "\n" in k or "\r" in v or "\n" in v) for k, v in headers.items()):
                return failed("Header names and values cannot contain line breaks.")
            req_headers.update(headers)

        try:
            body = json.dumps(payload).encode("utf-8")
        except (TypeError, ValueError) as exc:
            return failed(f"payload is not JSON-serializable: {exc}")
        if len(body) > _MAX_PAYLOAD_BYTES:
            return failed(f"payload is too large (limit {_MAX_PAYLOAD_BYTES} bytes).")

        try:
            req = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
            with open_guarded(req, timeout=timeout_seconds, follow_redirects=False) as resp:
                code = resp.getcode()
                return {
                    "success": isinstance(code, int) and 200 <= code < 300,
                    "status_code": code,
                    "url": shown,
                }
        except UrlRefused as exc:
            return failed(f"URL refused: {exc}")
        except urllib.error.HTTPError as exc:
            return failed(str(exc), exc.code)
        except Exception as exc:
            return failed(f"{type(exc).__name__}: {exc}")

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
        """Build a notification and POST it to ``webhook_url``.

        Without a ``webhook_url`` nothing is sent: the result is ``dispatched: False`` /
        ``status: "not_sent"`` and carries the payload, so the caller can deliver it itself.
        """
        payload = cls.build_payload(
            title=title,
            message=message,
            event_type=event_type,
            priority=priority,
        )

        if not webhook_url:
            return {
                "dispatched": False,
                "channel": "none",
                "status": "not_sent",
                "error": "No webhook_url was given, so nothing was sent anywhere.",
                "payload": payload,
            }

        res = cls.dispatch_to_webhook(webhook_url, payload, timeout_seconds=timeout_seconds)
        return {
            "dispatched": res["success"],
            "channel": "webhook",
            "status_code": res.get("status_code"),
            "status": "delivered" if res["success"] else "failed",
            "error": res.get("error"),
            "destination": res.get("url"),
            "payload": payload,
        }
