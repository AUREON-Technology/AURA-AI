"""Cloudflare ``clairic`` as a :class:`~aura_core.ports.extraction.StructuredExtractor` (ADR-038).

``clairic`` reads a body of text and fills typed fields, returning each answer with a confidence.
It is reached at ``POST /client/v4/accounts/<id>/ai/run/@cf/cloudflare/clairic`` -- not the
OpenAI-compatible ``/ai/v1`` path -- and wraps its answer in ``{"result": ...}``.

This is the **only** module that names that path (``TestExtractionIsGatedNotSilent`` pins it). The
host and the model come from :mod:`aura_core.config.external_inference` and are not configurable:
no ``base_url``, no model setting, and redirects are never followed, so the credential cannot be
sent anywhere else.

What it will not do:

* **Log or return the text, the answers or the credential.** Errors carry a status and Cloudflare's
  numeric error codes, never its messages (which can echo input) and never a response body.
* **Retry.** One call, one request. A 429 or a failure opens a short cooldown during which later
  calls are refused without touching the network, so a struggling preview model is not hammered
  and the shared daily allowance is not burned on repeats.
* **Trust an answer.** Every raw answer goes through
  :func:`~aura_core.domain.extraction.classify_answer`.

``httpx`` is imported inside the function that builds the client, like every other adapter.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from ..config.external_inference import EXTRACTION_API_HOST, EXTRACTION_MODEL, is_cloudflare_account_id
from ..domain.errors import ConfigurationError, ExtractionError, RateLimitedError
from ..domain.extraction import (
    ExtractedField,
    ExtractionRequest,
    ExtractionResult,
    FieldSpec,
    FieldType,
    classify_answer,
)
from ..ports.llm import HealthStatus
from ..ports.runtime import Clock

__all__ = ["ClairicExtractor"]

#: The model selector the service wants in the body (it accepts this or the full ``@cf/`` id).
_SELECTOR = "clairic"
#: A response larger than this is refused unread: an answer is a few KB.
_MAX_RESPONSE_BYTES = 1_000_000
_MAX_ERROR_CODES = 5
_MAX_RETRY_AFTER_S = 3600
_DEFAULT_COOLDOWN_S = 60.0
_ERROR_COOLDOWN_S = 10.0


def _wire_spec(spec: FieldSpec) -> dict[str, Any]:
    """The service's field spec for one of ours. Only keys the service documents are sent."""
    if spec.type is FieldType.TEXT_LIST:
        out: dict[str, Any] = {"type": "list", "items": "text"}
    else:
        out = {"type": spec.type.value}
    if spec.type is FieldType.ENUM:
        out["enum"] = list(spec.enum_values)
    if spec.type is FieldType.NUMBER:
        if spec.integer:
            out["integer"] = True
        if spec.decimals is not None:
            out["decimals"] = spec.decimals
        if spec.unit:
            out["unit"] = spec.unit
    if spec.instructions:
        out["instructions"] = spec.instructions
    return out


def _error_codes(body: Any) -> list[int]:
    """Cloudflare's numeric error codes from an error body, and nothing else from it."""
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return []
    codes = [e["code"] for e in errors if isinstance(e, dict) and isinstance(e.get("code"), int)]
    return codes[:_MAX_ERROR_CODES]


class ClairicExtractor:
    """``transport`` is for tests only (an ``httpx.MockTransport``); production passes ``None``."""

    def __init__(
        self,
        *,
        account_id: str,
        api_key: str,
        clock: Clock,
        timeout_s: float = 30.0,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
        transport: Any = None,
    ) -> None:
        if not is_cloudflare_account_id(account_id):
            raise ConfigurationError(
                "AURA_EXTRACT_CLOUDFLARE_ACCOUNT_ID must be the 32-character hexadecimal Cloudflare "
                "account id"
            )
        if not isinstance(api_key, str) or not api_key.strip():
            raise ConfigurationError(
                "the extraction credential is empty: set the variable named by AURA_EXTRACT_API_KEY_ENV"
            )
        if not (isinstance(timeout_s, (int, float)) and 0 < timeout_s <= 300):
            raise ConfigurationError("AURA_EXTRACT_TIMEOUT_S must be between 0 and 300 seconds")
        self._url = f"https://{EXTRACTION_API_HOST}/client/v4/accounts/{account_id}/ai/run/{EXTRACTION_MODEL}"
        self._api_key = api_key.strip()
        self._clock = clock
        self._timeout_s = float(timeout_s)
        self._cooldown_s = float(cooldown_s)
        self._transport = transport
        self._client: Any = None
        self._cooldown_until = 0.0

    def __repr__(self) -> str:
        return f"ClairicExtractor(model={EXTRACTION_MODEL!r})"

    @property
    def name(self) -> str:
        return "clairic"

    @property
    def model_id(self) -> str:
        return EXTRACTION_MODEL

    def _get_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=min(10.0, self._timeout_s)),
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                transport=self._transport,
                follow_redirects=False,
            )
        return self._client

    def _remaining_cooldown(self) -> float:
        return max(0.0, self._cooldown_until - self._clock.monotonic())

    def _open_cooldown(self, seconds: float) -> None:
        self._cooldown_until = max(self._cooldown_until, self._clock.monotonic() + seconds)

    async def health(self) -> HealthStatus:
        remaining = self._remaining_cooldown()
        if remaining > 0:
            return HealthStatus(False, f"cooling down for {math.ceil(remaining)}s")
        return HealthStatus(True, "configured; not probed (no free endpoint)")

    async def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            await client.aclose()

    async def extract(self, request: ExtractionRequest, *, min_confidence: float) -> ExtractionResult:
        remaining = self._remaining_cooldown()
        if remaining > 0:
            raise RateLimitedError(
                "structured extraction is cooling down after an upstream problem",
                retry_after=math.ceil(remaining),
            )
        body = {
            "model": _SELECTOR,
            "state": request.text,
            "fields": {spec.name: _wire_spec(spec) for spec in request.fields},
        }
        try:
            response = await self._get_client().post(self._url, json=body)
        except Exception as exc:
            self._open_cooldown(_ERROR_COOLDOWN_S)
            raise ExtractionError(
                f"the extraction request failed ({type(exc).__name__})",
                details={"error": type(exc).__name__},
            ) from None  # not `from exc`: the cause's text could carry the request

        status = response.status_code
        if status == 429:
            retry_after = self._retry_after(response)
            self._open_cooldown(retry_after)
            raise RateLimitedError("Cloudflare rate-limited the extraction request", retry_after=retry_after)
        if not 200 <= status < 300:
            codes = self._codes(response)
            if status >= 500:
                self._open_cooldown(_ERROR_COOLDOWN_S)
            raise ExtractionError(
                f"Cloudflare answered the extraction request with HTTP {status}",
                retryable=status >= 500,
                details={"status": status, "codes": codes},
            )
        return self._parse(request, response, min_confidence)

    def _retry_after(self, response: Any) -> int:
        raw = response.headers.get("retry-after", "")
        try:
            seconds = int(float(raw))
        except (TypeError, ValueError):
            return int(self._cooldown_s)
        return min(max(seconds, 1), _MAX_RETRY_AFTER_S)

    @staticmethod
    def _codes(response: Any) -> list[int]:
        try:
            return _error_codes(response.json())
        except ValueError:
            return []

    def _parse(self, request: ExtractionRequest, response: Any, min_confidence: float) -> ExtractionResult:
        if len(response.content) > _MAX_RESPONSE_BYTES:
            raise ExtractionError("the extraction response was too large to read", retryable=False)
        try:
            payload = response.json()
        except ValueError:
            payload = None
        result = payload.get("result") if isinstance(payload, dict) else None
        answers = result.get("answers") if isinstance(result, dict) else None
        if not (isinstance(payload, dict) and payload.get("success") is True and isinstance(answers, dict)):
            raise ExtractionError("the extraction response could not be read", details={"status": response.status_code})

        fields: dict[str, ExtractedField] = {}
        for spec in request.fields:
            fields[spec.name] = self._classify(spec, answers.get(spec.name), min_confidence)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        model = result.get("model")
        return ExtractionResult(
            fields=fields,
            truncated=bool(result.get("truncated")),
            model=model[:60] if isinstance(model, str) else EXTRACTION_MODEL,
            input_tokens=self._count(usage.get("input_tokens")),
            output_tokens=self._count(usage.get("output_tokens")),
        )

    @staticmethod
    def _count(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None

    @staticmethod
    def _classify(spec: FieldSpec, answer: Any, min_confidence: float) -> ExtractedField:
        if not isinstance(answer, Mapping):
            return classify_answer(spec, value=None, confidence=None, min_confidence=min_confidence, valid=False)
        # ``valid`` and ``complete`` are present on generated answers only; a typed answer (a date)
        # carries neither, so absence means "nothing to object to", and an explicit false means invalid.
        ok = answer.get("valid") is not False and answer.get("complete") is not False
        return classify_answer(
            spec,
            value=answer.get("value"),
            confidence=answer.get("confidence"),
            min_confidence=min_confidence,
            valid=ok,
            span=answer.get("span"),
        )
