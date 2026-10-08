"""The gateway as the load generator sees it: timed requests, retries, and SSE turns.

Every call returns a :class:`~.stats.Sample` no matter what happened -- a timeout, a refused
connection, a 429 from nginx, an ``error`` frame mid-stream -- because a load test that drops its
failures on the floor reports the survivors' latency as the system's.
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from .auth import TokenSource
from .config import RunConfig
from .sse import SseParser
from .stats import (
    ERR_CONNECTION,
    ERR_TIMEOUT,
    Sample,
    classify_status,
    classify_stream_error,
)

#: Statuses worth retrying (when ``max_retries`` > 0): the server said "not now", not "no".
_RETRYABLE = {429, 502, 503, 504}

#: Below this generation span, tokens/sec is noise (and an in-process echo stream arrives in
#: one buffered chunk, giving a zero span) -- report nothing rather than a nonsense rate.
_MIN_GENERATION_S = 0.05


@dataclass
class VU:
    """One virtual user within one matrix step."""

    index: int
    rng: random.Random
    session_id: str
    t0: float
    state: dict[str, Any] = field(default_factory=dict)

    def now(self) -> float:
        return time.perf_counter() - self.t0


@dataclass
class StreamResult:
    text: str = ""
    done: Mapping[str, Any] | None = None
    steps: int = 0


@dataclass
class ResponseResult:
    body: Any = None
    content: bytes = b""


class Gateway:
    def __init__(self, client: Any, cfg: RunConfig, tokens: TokenSource) -> None:
        self.client = client
        self.cfg = cfg
        self.tokens = tokens
        self.counters: dict[str, int] = {}
        #: The voice scenario's upload, loaded once at preflight (see ``wav.load_wav``).
        self.voice_wav: bytes = b""

    def count(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n

    def _headers(self, vu: VU) -> dict[str, str]:
        headers = self.tokens.headers(vu.index)
        headers["X-Correlation-Id"] = f"lt-{vu.session_id}-{int(time.time() * 1000) % 10**9}"
        return headers

    def _timeout(self, total: float) -> Any:
        import httpx

        return httpx.Timeout(total, connect=self.cfg.connect_timeout_s)

    async def _backoff(self, attempt: int, retry_after: str | None) -> None:
        delay = 0.5 * (2 ** attempt)
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                pass
        await asyncio.sleep(min(max(delay, 0.0), self.cfg.retry_cap_s))

    # -- plain requests ----------------------------------------------------------------

    async def request(
        self, vu: VU, *, scenario: str, op: str, kind: str, method: str, path: str,
        json_body: Any = None, files: Any = None, params: Mapping[str, Any] | None = None,
        timeout_s: float | None = None,
    ) -> tuple[Sample, ResponseResult]:
        import httpx

        total = timeout_s or self.cfg.api_timeout_s
        started_s = vu.now()
        began = time.perf_counter()
        retries = 0
        result = ResponseResult()
        while True:
            status: int | None = None
            error: str | None = None
            detail: str | None = None
            retry_after = None
            try:
                response = await asyncio.wait_for(
                    self.client.request(
                        method, path, json=json_body, files=files, params=params,
                        headers=self._headers(vu), timeout=self._timeout(total),
                    ),
                    timeout=total,
                )
                status = response.status_code
                result.content = response.content
                error = classify_status(status)
                if error is None:
                    ctype = response.headers.get("content-type", "")
                    if "json" in ctype:
                        result.body = response.json()
                else:
                    detail = error_detail(response.content, status)
                    retry_after = response.headers.get("retry-after")
            except (asyncio.TimeoutError, httpx.TimeoutException):
                error, detail = ERR_TIMEOUT, f"no response within {total:.0f}s"
            except httpx.TransportError as exc:
                error, detail = ERR_CONNECTION, type(exc).__name__
            if self._should_retry(error, status, retries):
                retries += 1
                await self._backoff(retries - 1, retry_after)
                continue
            break
        return (
            Sample(
                scenario=scenario, op=op, kind=kind, vu=vu.index, started_s=started_s,
                e2e_ms=(time.perf_counter() - began) * 1000.0, status=status, error=error,
                detail=detail, retries=retries,
            ),
            result,
        )

    def _should_retry(self, error: str | None, status: int | None, retries: int) -> bool:
        if error is None or retries >= self.cfg.max_retries:
            return False
        return error == ERR_CONNECTION or (status in _RETRYABLE)

    # -- streamed turns ----------------------------------------------------------------

    async def chat_stream(
        self, vu: VU, *, scenario: str, op: str, payload: Mapping[str, Any],
    ) -> tuple[Sample, StreamResult]:
        """One ``POST /api/v3/chat/stream`` turn, timed to the first ``token`` frame (TTFT)
        and to the end of the stream (E2E)."""
        import httpx

        total = self.cfg.request_timeout_s
        started_s = vu.now()
        began = time.perf_counter()
        retries = 0
        while True:
            state: dict[str, Any] = {
                "status": None, "error": None, "detail": None, "ttft": None,
                "frames": 0, "retry_after": None,
            }
            result = StreamResult()
            try:
                await asyncio.wait_for(
                    self._consume_stream(vu, payload, began, state, result, total), timeout=total
                )
            except (asyncio.TimeoutError, httpx.TimeoutException):
                state["error"], state["detail"] = ERR_TIMEOUT, f"stream not finished within {total:.0f}s"
            except httpx.TransportError as exc:
                state["error"], state["detail"] = ERR_CONNECTION, type(exc).__name__
            # Retry only a refusal that arrived before any output: once a token has reached the
            # caller, re-sending would duplicate what they already saw (the same rule the
            # gateway's own router follows for provider failover).
            if state["ttft"] is None and state["frames"] == 0 and self._should_retry(
                state["error"], state["status"], retries
            ):
                retries += 1
                await self._backoff(retries - 1, state["retry_after"])
                continue
            break

        e2e_ms = (time.perf_counter() - began) * 1000.0
        completion_tokens = None
        tokens_per_s = None
        if result.done is not None:
            usage = result.done.get("usage") or {}
            if isinstance(usage, Mapping) and usage.get("completion_tokens"):
                completion_tokens = int(usage["completion_tokens"])
                if state["ttft"] is not None:
                    span_s = (e2e_ms - state["ttft"]) / 1000.0
                    if span_s >= _MIN_GENERATION_S:
                        tokens_per_s = completion_tokens / span_s
        return (
            Sample(
                scenario=scenario, op=op, kind="llm", vu=vu.index, started_s=started_s,
                e2e_ms=e2e_ms, status=state["status"], error=state["error"],
                detail=state["detail"], ttft_ms=state["ttft"], retries=retries,
                completion_tokens=completion_tokens, tokens_per_s=tokens_per_s,
                frames=state["frames"], steps=result.steps,
            ),
            result,
        )

    async def _consume_stream(
        self, vu: VU, payload: Mapping[str, Any], began: float, state: dict[str, Any],
        result: StreamResult, total: float,
    ) -> None:
        headers = self._headers(vu)
        headers["Accept"] = "text/event-stream"
        async with self.client.stream(
            "POST", "/api/v3/chat/stream", json=dict(payload), headers=headers,
            timeout=self._timeout(total),
        ) as response:
            state["status"] = response.status_code
            if response.status_code != 200:
                body = await response.aread()
                state["error"] = classify_status(response.status_code)
                state["detail"] = error_detail(body, response.status_code)
                state["retry_after"] = response.headers.get("retry-after")
                return
            parser = SseParser()
            collected: list[str] = []
            async for chunk in response.aiter_text():
                for frame in parser.feed(chunk):
                    self._on_frame(frame, began, state, result, collected)
            for frame in parser.flush():
                self._on_frame(frame, began, state, result, collected)
            if result.text == "":
                result.text = "".join(collected)
        if state["error"] is None and result.done is None:
            state["error"] = ERR_CONNECTION
            state["detail"] = "stream ended without a done frame"

    @staticmethod
    def _on_frame(
        frame: Mapping[str, Any], began: float, state: dict[str, Any], result: StreamResult,
        collected: list[str],
    ) -> None:
        state["frames"] += 1
        kind = frame.get("type")
        if kind == "token":
            text = frame.get("text") or ""
            if text and state["ttft"] is None:
                state["ttft"] = (time.perf_counter() - began) * 1000.0
            collected.append(text)
        elif kind == "step":
            result.steps += 1
        elif kind == "revision":
            result.text = str(frame.get("text") or "")
        elif kind == "error":
            if state["error"] is None:
                state["error"] = classify_stream_error(frame.get("error"))
                state["detail"] = f"stream error frame: {frame.get('error')}"
        elif kind == "done":
            result.done = frame


def error_detail(body: bytes, status: int | None) -> str:
    """A short, secret-free description of an error response for the report's error table."""
    try:
        parsed = json.loads(body.decode("utf-8", errors="replace")) if body else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = None
    if isinstance(parsed, Mapping):
        inner = parsed.get("detail", parsed)
        if isinstance(inner, Mapping) and inner.get("error"):
            return str(inner["error"])
        if isinstance(parsed.get("error"), str):
            return str(parsed["error"])
        if isinstance(inner, str):
            return inner[:80]
        return f"HTTP {status}"
    if status in (429, 503) and body:
        # nginx's limit_req/limit_conn answer with an HTML page, not the gateway's JSON body.
        return "non-JSON body (edge proxy limit_req/limit_conn?)"
    return f"HTTP {status}"


__all__ = ["Gateway", "VU", "StreamResult", "ResponseResult", "error_detail"]
