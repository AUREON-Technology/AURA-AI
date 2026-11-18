"""WebSocket adapter for Phase H live voice (``/api/v3/voice/live``).

Kept out of ``v3.py`` the same way ``register_admin_ops`` is. Auth reuses the injected
``authenticator`` via a small header-carrier so JWT verification stays one path.

NOTE: deliberately no ``from __future__ import annotations`` — FastAPI must see a real
``WebSocket`` type on the handler or it mis-reads the parameter as a query field.
"""

import asyncio
import json
from typing import Any, Callable, Mapping

from ..domain.errors import AuthenticationError
from ..domain.identifiers import SessionId
from ..observability.logging import get_logger
from ..voice import LiveVoiceConfig, LiveVoiceSession, PcmFrameError
from . import ws_auth
from .ws_auth import LiveCredential

logger = get_logger("api.v3.voice_live")

# WebSocket close codes (application-defined 4xxx).
_CLOSE_UNAUTHORIZED = 4401
_CLOSE_TOO_MANY = 4429
_CLOSE_UNAVAILABLE = 4503
_CLOSE_BAD_REQUEST = 4400

_AUTH_TIMEOUT_S = 5.0
_SESSION_TTL_S = 60 * 20  # slightly longer than 15 min access TTL


class _HeaderCarrier:
    """Minimal stand-in for Starlette Request headers for the authenticator."""

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = None


class _SessionRegistry:
    """Per-user + global live-session caps via the cache port (Redis when configured).

    When the cache is in-process, the advertised global cap is effectively × worker count —
    documented honestly rather than papered over.
    """

    def __init__(self, cache: Any, *, max_sessions: int) -> None:
        self._cache = cache
        self._max = max_sessions
        self._local: set[str] = set()

    async def acquire(self, user_id: str) -> bool:
        user_key = f"voice_live:user:{user_id}"
        global_key = "voice_live:global"
        try:
            existing = await self._cache.get(user_key)
            if existing is not None:
                return False
            global_count = await self._cache.incr(global_key, ttl_s=_SESSION_TTL_S)
            if global_count > self._max:
                # Roll global back by rewriting a lower value when possible.
                await self._cache.set(
                    global_key, str(max(0, global_count - 1)).encode(), ttl_s=_SESSION_TTL_S
                )
                return False
            await self._cache.set(user_key, b"1", ttl_s=_SESSION_TTL_S)
            self._local.add(user_id)
            return True
        except Exception:
            if user_id in self._local or len(self._local) >= self._max:
                return False
            self._local.add(user_id)
            return True

    async def release(self, user_id: str) -> None:
        self._local.discard(user_id)
        try:
            await self._cache.delete(f"voice_live:user:{user_id}")
            raw = await self._cache.get("voice_live:global")
            if raw is not None:
                try:
                    current = int(raw.decode() if isinstance(raw, (bytes, bytearray)) else raw)
                except (TypeError, ValueError):
                    current = 1
                await self._cache.set(
                    "voice_live:global",
                    str(max(0, current - 1)).encode(),
                    ttl_s=_SESSION_TTL_S,
                )
        except Exception:
            pass


def register_voice_live(
    router: Any,
    container: Any,
    *,
    authenticator: Callable,
    rate_limiter: Any,
    recheck_s: float | None = None,
) -> None:
    from fastapi import WebSocket
    from starlette.websockets import WebSocketDisconnect

    @router.websocket("/voice/live")
    async def voice_live(websocket: WebSocket) -> None:
        try:
            await websocket.accept()
        except Exception:
            logger.exception("voice live accept failed")
            return
        live = getattr(container, "live_voice", None)
        if not live or not live.get("enabled"):
            # Engines absent or feature off — refuse rather than silently degrade.
            await websocket.close(code=_CLOSE_UNAVAILABLE)
            return

        try:
            principal, token = await _authenticate_ws(websocket, authenticator)
        except AuthenticationError:
            await websocket.close(code=_CLOSE_UNAUTHORIZED)
            return
        except Exception:
            logger.exception("voice live auth failed")
            await websocket.close(code=_CLOSE_UNAUTHORIZED)
            return

        registry = _SessionRegistry(live["cache"], max_sessions=int(live["max_sessions"]))
        user_key = principal.user_id.value
        if not await registry.acquire(user_key):
            await websocket.close(code=_CLOSE_TOO_MANY)
            return

        session: LiveVoiceSession | None = None
        pump: asyncio.Task[None] | None = None
        try:
            if rate_limiter is not None:
                decision = await rate_limiter.check_all(
                    user_key=principal.user_id.value,
                    ip_key=_ws_client_ip(websocket),
                    route_class="voice_live",
                )
                if not decision.allowed:
                    await websocket.close(code=_CLOSE_TOO_MANY)
                    return

            # Wait for start frame.
            start = await _recv_json(websocket, timeout=_AUTH_TIMEOUT_S)
            if not start or start.get("type") != "start":
                await websocket.close(code=_CLOSE_BAD_REQUEST)
                return

            session_id = str(start.get("session_id") or "").strip()
            if not session_id:
                await websocket.send_json(
                    {"type": "error", "error": "validation", "message": "session_id required"}
                )
                await websocket.close(code=_CLOSE_BAD_REQUEST)
                return

            language = str(start.get("language") or "en")
            voice_name = start.get("voice")
            cfg: LiveVoiceConfig = live["config"]
            session = LiveVoiceSession(
                stt=live["stt"],
                tts=live["tts"],
                turn_streamer=live["kernel"],
                user_id=principal.user_id,
                session_id=SessionId(session_id),
                nlp=live.get("nlp"),
                config=cfg,
                language=language,
                voice=voice_name,
                capabilities=_granted(container, principal),
                safety_guards_enabled=bool(live.get("safety_guards_enabled", True)),
                vad=(live.get("vad_factory") or (lambda: None))(),
            )
            lang_err = session.check_language()
            if lang_err:
                await websocket.send_json(
                    {"type": "error", "error": "language_unsupported", "message": lang_err}
                )
                await websocket.close(code=_CLOSE_BAD_REQUEST)
                return

            await session.start()
            pump = asyncio.create_task(_pump_outbound(websocket, session))

            credential = LiveCredential(
                token=token, principal=principal, recheck_s=recheck_s or ws_auth.DEFAULT_RECHECK_S,
                check=lambda t: _authenticate_frame({"token": t}, authenticator),
            )
            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.receive(), timeout=credential.seconds_until_due() or 0.001
                    )
                except asyncio.TimeoutError:
                    message = None
                if not await credential.still_valid(lambda _p: True):
                    # The token has expired or been revoked and no reauth renewed it. The client
                    # sends one after every turn, so this ends idle sessions, not live ones.
                    await websocket.close(code=_CLOSE_UNAUTHORIZED)
                    return
                if message is None:
                    continue
                if message.get("type") == "websocket.disconnect":
                    break
                if "bytes" in message and message["bytes"] is not None:
                    try:
                        await session.push_pcm(message["bytes"])
                    except PcmFrameError as exc:
                        # An oversized frame is a protocol violation, not audio (FSA-007).
                        logger.warning(
                            "voice live: closing session on invalid pcm frame",
                            extra={"reason": str(exc), "user": principal.user_id.value},
                        )
                        await websocket.close(code=_CLOSE_BAD_REQUEST)
                        return
                    continue
                text = message.get("text")
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                ftype = frame.get("type")
                if ftype == "stop":
                    break
                if ftype == "barge_in":
                    await session.barge_in()
                elif ftype == "commit":
                    await session.commit()
                elif ftype == "confirm":
                    # F-04: confirmation is by the approval ids this session was given, never by
                    # tool name -- a `tools` list from an older client is ignored, and with no
                    # ids the session approves exactly the turn it is holding.
                    raw_ids = frame.get("approval_ids")
                    approval_ids = (
                        [str(i) for i in raw_ids][:32] if isinstance(raw_ids, list) else None
                    )
                    if rate_limiter is not None:
                        decision = await rate_limiter.check_all(
                            user_key=principal.user_id.value,
                            ip_key=_ws_client_ip(websocket),
                            route_class="voice_live",
                        )
                        if not decision.allowed:
                            await websocket.send_json(
                                {"type": "error", "error": "rate_limited", "message": "budget"}
                            )
                            continue
                    await session.confirm(approval_ids)
                elif ftype == "reject":
                    await session.reject()
                elif ftype == "reauth":
                    try:
                        renewed = await _authenticate_frame(frame, authenticator)
                    except AuthenticationError:
                        await websocket.close(code=_CLOSE_UNAUTHORIZED)
                        return
                    if renewed.user_id != principal.user_id:
                        # A reauth renews *this* user's session; it is not a way to keep a socket
                        # opened with someone else's (possibly stolen) token alive with your own.
                        await websocket.close(code=_CLOSE_UNAUTHORIZED)
                        return
                    principal = renewed
                    credential.renew(str(frame.get("token") or "").strip(), renewed)
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("voice live session failed")
            try:
                await websocket.send_json(
                    {"type": "error", "error": "internal_error", "message": "session failed"}
                )
            except Exception:
                pass
        finally:
            if session is not None:
                await session.close()
            if pump is not None and not pump.done():
                pump.cancel()
                try:
                    await pump
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            await registry.release(user_key)
            try:
                await websocket.close()
            except Exception:
                pass


async def _pump_outbound(websocket: Any, session: LiveVoiceSession) -> None:
    async for item in session.events():
        if isinstance(item, (bytes, bytearray)):
            await websocket.send_bytes(bytes(item))
        else:
            await websocket.send_json(dict(item))


async def _authenticate_ws(websocket: Any, authenticator: Callable) -> tuple[Any, str]:
    frame = await _recv_json(websocket, timeout=_AUTH_TIMEOUT_S)
    if not frame or frame.get("type") != "auth":
        raise AuthenticationError("auth frame required")
    return await _authenticate_frame(frame, authenticator), str(frame.get("token") or "").strip()


async def _authenticate_frame(frame: Mapping[str, Any], authenticator: Callable) -> Any:
    token = str(frame.get("token") or "").strip()
    if not token:
        raise AuthenticationError("token required")
    headers = {"authorization": f"Bearer {token}"}
    # Attestation parity with VoiceClient HTTP headers (enforced at token exchange, not here).
    for key, value in (frame.get("attestation") or {}).items():
        headers[str(key).lower()] = str(value)
    carrier = _HeaderCarrier(headers)
    return await authenticator(carrier)


async def _recv_json(websocket: Any, *, timeout: float) -> dict[str, Any] | None:
    try:
        raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    except Exception:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _ws_client_ip(websocket: Any) -> str:
    client = getattr(websocket, "client", None)
    if client is not None and getattr(client, "host", None):
        return str(client.host)
    return "0.0.0.0"


def _granted(container: Any, principal: Any) -> tuple[str, ...]:
    """The caller's capabilities plus what the live blueprint binds their role to (AP10) -- the
    same expansion ``/chat`` applies, so a voice turn reaches exactly the tools a typed one does."""
    deployment = getattr(container, "blueprint_deployment", None)
    capabilities = tuple(principal.capabilities)
    if deployment is None or getattr(principal, "is_service", False):
        return capabilities
    return deployment.caller_capabilities(capabilities, getattr(principal, "role", ""))
