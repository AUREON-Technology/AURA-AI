"""WebSocket adapter for the desktop Engineer agent's host daemon (``/api/v3/host/agent``).

Shaped like ``api/voice_live.py``: accept, then an auth frame authenticated through the same
injected ``authenticator`` FastAPI uses everywhere else, then a message loop -- but where
voice/live streams audio server-to-client, this route is a duplex RPC channel. The desktop daemon
(``aura_hostd``) opens the connection outbound (so a home network with no inbound port works);
the server then *calls into it*, via ``adapters/host_agent.py``'s ``HostAgentSession``/
``HostAgentRegistry``, exactly the same way ``build_sandbox_tools`` already calls into
``DockerSandboxRunner`` -- the tool layer does not know or care which one it is talking to.

See ``docs/architecture/011-desktop-engineer-agent.md`` for why execution moves to the user's own
machine here rather than staying in a server-side container, and why that does not weaken
ADR-006: server-side ``ToolPolicy`` still evaluates every call before this route is ever reached.

NOTE: deliberately no ``from __future__ import annotations`` -- same FastAPI ``WebSocket``-typing
reason ``voice_live.py`` documents.
"""

import asyncio
import json
from typing import Any, Callable, Mapping

from ..adapters.host_agent import HostAgentSession
from ..domain.errors import AuthenticationError
from ..observability.logging import get_logger
from ..security.role_capabilities import ENGINEER_AGENT
from . import ws_auth
from .ws_auth import LiveCredential

logger = get_logger("api.v3.host_agent")

# WebSocket close codes (application-defined 4xxx) -- same numbering convention as voice_live.py.
_CLOSE_UNAUTHORIZED = 4401
#: RFC 6455 "internal error": the daemon retries with backoff rather than giving up (WP9).
_CLOSE_SERVER_ERROR = 1011
_CLOSE_UNAVAILABLE = 4503
_CLOSE_BAD_REQUEST = 4400

_AUTH_TIMEOUT_S = 5.0


class _HeaderCarrier:
    """Minimal stand-in for Starlette Request headers for the authenticator. Same shape as
    voice_live.py's -- kept local rather than shared, matching this codebase's existing pattern of
    each `api/` module owning its own small WS-auth plumbing (see also `admin.py`, `v3.py`)."""

    #: Device tokens minted by pairing carry ``scope: host_agent`` and are refused everywhere
    #: except this socket (``services/jwt_auth.verify_access_token``).
    allowed_token_scopes = ("host_agent",)

    def __init__(self, headers: Mapping[str, str]) -> None:
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.client = None


def may_host(principal: Any) -> bool:
    """Whether ``principal`` may serve this socket. Also what ``/auth/desktop/pair/approve``
    checks before pairing a daemon (audit INT-003), so the two can never disagree."""
    return ENGINEER_AGENT in principal.capabilities


def register_host_agent(
    router: Any, container: Any, *, authenticator: Callable,
    recheck_s: float | None = None,
) -> None:
    from fastapi import WebSocket
    from starlette.websockets import WebSocketDisconnect

    @router.websocket("/host/agent")
    async def host_agent(websocket: WebSocket) -> None:
        try:
            await websocket.accept()
        except Exception:
            logger.exception("host agent accept failed")
            return

        registry = getattr(container, "host_agent_registry", None)
        if registry is None:
            await websocket.close(code=_CLOSE_UNAVAILABLE)
            return

        try:
            principal, token = await _authenticate_ws(websocket, authenticator)
        except AuthenticationError:
            await websocket.close(code=_CLOSE_UNAUTHORIZED)
            return
        except Exception as exc:
            # G-12 (WP9): 4401 makes the daemon stop and ask to be paired again, so it is only
            # for a rejected credential -- including the 401/403 HTTPException the JWT layer
            # raises for an expired or revoked token. Anything else is this server failing, and
            # unpairing someone's computer over a database blip would be wrong: 1011 makes the
            # daemon reconnect with backoff instead.
            if getattr(exc, "status_code", None) in (401, 403):
                await websocket.close(code=_CLOSE_UNAUTHORIZED)
                return
            logger.exception("host agent auth failed")
            await websocket.close(code=_CLOSE_SERVER_ERROR)
            return

        if not may_host(principal):
            # A daemon can only ever be usefully paired to an account the server would actually
            # route engineer-agent tool calls to -- refuse it here rather than registering a
            # session nothing will ever call.
            await websocket.close(code=_CLOSE_UNAUTHORIZED)
            return

        async def send(frame: Mapping[str, Any]) -> None:
            await websocket.send_json(dict(frame))

        session = HostAgentSession(principal.user_id, send)
        credential = LiveCredential(
            token=token, principal=principal, recheck_s=recheck_s or ws_auth.DEFAULT_RECHECK_S,
            check=lambda t: _authenticate_frame({"token": t}, authenticator),
        )
        await registry.register(session)
        logger.info("host agent connected", extra={"user_id": principal.user_id.value})
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        websocket.receive(), timeout=credential.seconds_until_due() or 0.001
                    )
                except asyncio.TimeoutError:
                    message = None
                if not await credential.still_valid(may_host):
                    # Expired, device revoked, or the account no longer may host: the same
                    # answer an HTTP request with this token would now get.
                    await websocket.close(code=_CLOSE_UNAUTHORIZED)
                    break
                if message is None:
                    continue
                if message.get("type") == "websocket.disconnect":
                    break
                text = message.get("text")
                if not text:
                    continue
                try:
                    frame = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(frame, dict):
                    continue

                ftype = frame.get("type")
                if ftype in ("result", "error", "stream"):
                    # "stream" is a run_streaming increment (adapters/host_agent.py's
                    # HostAgentSession.call_streaming); routed the same way as result/error since
                    # resolve() itself decides whether a call_id is a streaming or single-shot
                    # call.
                    session.resolve(frame)
                elif ftype == "reauth":
                    try:
                        renewed = await _authenticate_frame(frame, authenticator)
                    except AuthenticationError:
                        break
                    if renewed.user_id != principal.user_id:
                        # The session is registered under the original user; a token for anyone
                        # else must end it, not quietly extend it (same rule as voice_live.py).
                        break
                    if not may_host(renewed):
                        break
                    credential.renew(str(frame.get("token") or ""), renewed)
                # Any other frame type is ignored -- forward compatible with a newer daemon that
                # sends frames this version does not understand, rather than dropping the session.
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("host agent session failed")
        finally:
            await registry.unregister(session)
            try:
                await websocket.close()
            except Exception:
                pass
            logger.info("host agent disconnected", extra={"user_id": principal.user_id.value})


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
