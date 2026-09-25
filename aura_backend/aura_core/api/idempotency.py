"""HTTP half of ``Idempotency-Key`` support (roadmap P1.4).

Two pieces, both built by :func:`build_idempotency` because FastAPI is imported lazily here the
same way ``v3.py`` does it:

* **a dependency** a route opts into with ``dependencies=[idempotent]``. It authenticates via the
  router's own ``principal`` dependency (FastAPI resolves that once per request, so nothing is
  verified twice), fingerprints the request, and claims the key. A completed key short-circuits
  into a replay; a running one is a 409; a reused one is a 422.
* **a route class** that sees the endpoint's final ``Response`` -- which no dependency can -- and
  settles the claim: a stored response for anything under 500, ``failed`` for a 5xx or a raised
  exception, so a failure stays retryable instead of being replayed forever.

The header is optional. Without it, or on a deployment whose container has no idempotency store,
every route behaves exactly as it did before this module existed -- the Flutter client does not
send the header today and must keep working unchanged.

**Streaming routes are deliberately not covered.** ``/chat/stream`` (SSE) and the voice/host
WebSockets deliver their output incrementally to a caller that has already rendered it; there is
no single response body to store and hand back, and replaying a recorded stream would re-render
text the user has already seen. A client that needs an exactly-once chat turn uses the non-
streaming ``POST /chat`` with a key; a streamed turn's side-effecting tools are protected instead
by the confirmation gate (``confirmed_call_ids``, ADR-006), which is itself keyed per call.
"""

# NOTE: deliberately no ``from __future__ import annotations`` -- same reason as v3.py: FastAPI
# resolves the guard's ``request: Request`` annotation at runtime, and ``Request`` is only in scope
# inside ``build_idempotency`` (imported lazily there).
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Callable

from ..domain.errors import ValidationError
from ..domain.identifiers import UserId
from ..domain.idempotency import (
    MAX_IDEMPOTENCY_KEY_LENGTH,
    ClaimOutcome,
    IdempotencyKeyInProgressError,
    IdempotencyKeyReusedError,
    StoredResponse,
)
from ..observability.logging import get_logger

__all__ = [
    "IDEMPOTENCY_HEADER",
    "REPLAYED_HEADER",
    "build_idempotency",
    "parse_idempotency_key",
]

logger = get_logger("api.idempotency")

IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"

#: Response headers worth replaying. Everything else (date, content-length, server) is either
#: recomputed by the server or would be a lie on the second answer.
_REPLAYED_RESPONSE_HEADERS = ("content-type", "x-correlation-id")

_STATE_ATTR = "aura_idempotency_claim"


def parse_idempotency_key(raw: str) -> str:
    """Validate an ``Idempotency-Key`` header value.

    The draft defines the value as a Structured Field *String* -- i.e. quoted, ``"8e03..."`` --
    but most clients in the wild (Stripe's convention) send it bare, so both are accepted and
    mean the same key. Printable ASCII only: the key lands in a database row and in log lines.
    """
    key = raw.strip()
    if len(key) >= 2 and key[0] == key[-1] == '"':
        key = key[1:-1]
    if not key or len(key) > MAX_IDEMPOTENCY_KEY_LENGTH:
        raise ValidationError(
            f"{IDEMPOTENCY_HEADER} must be 1-{MAX_IDEMPOTENCY_KEY_LENGTH} characters",
            details={"header": IDEMPOTENCY_HEADER},
        )
    if not all(0x21 <= ord(ch) <= 0x7E for ch in key):
        raise ValidationError(
            f"{IDEMPOTENCY_HEADER} must be printable ASCII without spaces",
            details={"header": IDEMPOTENCY_HEADER},
        )
    return key


async def _fingerprint(request: Any) -> str:
    """A stable digest of what makes two requests "the same request".

    Method, concrete path (so ``DELETE /memory/a`` and ``DELETE /memory/b`` differ), query string,
    and body. A JSON body is canonicalised (sorted keys, no whitespace) so a client that rebuilds
    its payload on retry and serialises the map in another order is still recognised; a form body
    hashes each field and each uploaded file's name, type and bytes. The body has already been
    read and cached by FastAPI before dependencies run, so this reads no stream twice.
    """
    digest = hashlib.sha256()
    for part in (request.method, request.url.path, request.url.query):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")

    content_type = (request.headers.get("content-type") or "").lower()
    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        form = await request.form()
        for name, value in sorted(form.multi_items(), key=lambda item: item[0]):
            digest.update(name.encode("utf-8"))
            digest.update(b"\x00")
            if hasattr(value, "read") and hasattr(value, "seek"):
                data = await value.read()
                await value.seek(0)  # the endpoint reads the same file after us
                digest.update((value.filename or "").encode("utf-8"))
                digest.update(b"\x00")
                digest.update((value.content_type or "").encode("utf-8"))
                digest.update(b"\x00")
                digest.update(hashlib.sha256(data).digest())
            else:
                digest.update(str(value).encode("utf-8"))
            digest.update(b"\x01")
        return digest.hexdigest()

    body = await request.body()
    if body and "json" in content_type:
        try:
            body = json.dumps(
                json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
        except ValueError:
            pass  # not valid JSON; the raw bytes are the fingerprint and the endpoint will 422
    digest.update(body)
    return digest.hexdigest()


@dataclass(slots=True)
class _Claim:
    """What the dependency hands the route class through ``request.state``."""

    store: Any
    clock: Any
    user_id: UserId
    operation: str
    key: str
    token: str

    async def settle(self, response: Any) -> None:
        body = getattr(response, "body", None)
        try:
            if response.status_code >= 500 or not isinstance(body, (bytes, bytearray)):
                # A 5xx is the server's failure and must stay retryable; a streaming response has
                # no body to store. Either way nothing is replayed.
                await self.fail()
                return
            headers = {
                name: response.headers[name]
                for name in _REPLAYED_RESPONSE_HEADERS
                if name in response.headers
            }
            await self.store.complete(
                user_id=self.user_id, operation=self.operation, key=self.key,
                claim_token=self.token,
                response=StoredResponse(
                    status_code=response.status_code, body=bytes(body), headers=headers
                ),
                now=self.clock.now(),
            )
        except Exception:
            # The operation itself succeeded; failing its response now would invite exactly the
            # retry this feature exists to absorb. The claim lapses after its lease instead.
            logger.warning(
                "could not record idempotent response",
                extra={"operation": self.operation}, exc_info=True,
            )

    async def fail(self) -> None:
        try:
            await self.store.fail(
                user_id=self.user_id, operation=self.operation, key=self.key,
                claim_token=self.token, now=self.clock.now(),
            )
        except Exception:
            logger.warning(
                "could not release idempotency claim",
                extra={"operation": self.operation}, exc_info=True,
            )


class _Replay(Exception):
    """Raised by the dependency to short-circuit the endpoint; caught by the route class."""

    def __init__(self, response: StoredResponse) -> None:
        super().__init__("idempotent replay")
        self.response = response


def build_idempotency(container: Any, *, principal: Callable) -> tuple[type, Any]:
    """Return ``(route_class, dependency)`` for a router.

    ``route_class`` goes on the ``APIRouter``; ``dependency`` (already wrapped in ``Depends``) goes
    in ``dependencies=[...]`` of each side-effecting route that should honour the header.
    """
    from fastapi import Depends, HTTPException, Request
    from fastapi.responses import Response
    from fastapi.routing import APIRoute

    security = container.settings.security
    ttl = timedelta(hours=security.idempotency_ttl_hours)
    lease = timedelta(seconds=security.idempotency_lease_s)

    async def idempotency_guard(request: Request, caller: Any = Depends(principal)) -> None:
        raw = request.headers.get(IDEMPOTENCY_HEADER)
        store = getattr(container, "idempotency", None)
        if raw is None or store is None:
            return
        try:
            key = parse_idempotency_key(raw)
        except ValidationError as exc:
            raise HTTPException(status_code=400, detail=exc.to_dict()) from exc

        route = request.scope.get("route")
        operation = f"{request.method} {getattr(route, 'path', request.url.path)}"
        result = await store.claim(
            user_id=caller.user_id, operation=operation, key=key,
            request_hash=await _fingerprint(request), now=container.clock.now(),
            ttl=ttl, lease=lease,
        )
        if result.outcome is ClaimOutcome.CLAIMED:
            setattr(
                request.state, _STATE_ATTR,
                _Claim(
                    store=store, clock=container.clock, user_id=caller.user_id,
                    operation=operation, key=key, token=result.claim_token,
                ),
            )
            return
        if result.outcome is ClaimOutcome.REPLAY and result.record.response is not None:
            raise _Replay(result.record.response)
        if result.outcome is ClaimOutcome.MISMATCH:
            error = IdempotencyKeyReusedError(
                f"this {IDEMPOTENCY_HEADER} was already used for a different request",
                details={"header": IDEMPOTENCY_HEADER},
            )
            raise HTTPException(status_code=error.http_status, detail=error.to_dict())
        error = IdempotencyKeyInProgressError(
            f"a request with this {IDEMPOTENCY_HEADER} is still being processed",
            details={"header": IDEMPOTENCY_HEADER},
        )
        raise HTTPException(
            status_code=error.http_status, detail=error.to_dict(), headers={"Retry-After": "1"},
        )

    class IdempotentRoute(APIRoute):
        def get_route_handler(self) -> Callable:
            handler = super().get_route_handler()

            async def run(request: Request) -> Any:
                try:
                    response = await handler(request)
                except _Replay as replay:
                    stored = replay.response
                    return Response(
                        content=stored.body,
                        status_code=stored.status_code,
                        headers={**dict(stored.headers), REPLAYED_HEADER: "true"},
                    )
                except BaseException:
                    claim = getattr(request.state, _STATE_ATTR, None)
                    if claim is not None:
                        await claim.fail()
                    raise
                claim = getattr(request.state, _STATE_ATTR, None)
                if claim is not None:
                    await claim.settle(response)
                return response

            return run

    return IdempotentRoute, Depends(idempotency_guard)
