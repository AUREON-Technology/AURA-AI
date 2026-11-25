"""The two ``StructuredExtractor`` adapters (ADR-038): the in-memory reader and Cloudflare's clairic.

The Cloudflare adapter is driven over ``httpx.MockTransport``, so the real client code runs and only
the network is replaced. ``PROBE`` is the response the live service returned on 2026-11-22.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import httpx
import pytest

from aura_core.adapters.cloudflare_clairic import ClairicExtractor
from aura_core.adapters.memory_extractor import InMemoryStructuredExtractor
from aura_core.adapters.runtime import FrozenClock
from aura_core.config.external_inference import (
    EXTRACTION_API_HOST,
    EXTRACTION_MODEL,
    is_external_inference_host,
    is_hosted_open_weight_host,
    is_open_weight_model,
)
from aura_core.domain.errors import (
    ConfigurationError,
    ExtractionError,
    RateLimitedError,
)
from aura_core.domain.extraction import ExtractionRequest, FieldSpec, FieldStatus, FieldType
from aura_core.ports import StructuredExtractor

ACCOUNT = "0123456789abcdef0123456789abcdef"
KEY = "cfat_not_a_real_token"
RUN_URL = f"https://{EXTRACTION_API_HOST}/client/v4/accounts/{ACCOUNT}/ai/run/@cf/cloudflare/clairic"

SAMPLE = (
    "Invoice INV-2291 from Northwind Supply, issued 2026-09-02, net 30. 4 pallets at $250.00 each, "
    "shipping $80.00. Contact: Dana Ruiz, Priya Shah."
)

#: What the live service returned for the five fields below (2026-11-22), trimmed of nothing that
#: the adapter reads. ``due_date`` is wrong (it is 2026-10-02) and says so at confidence 0.096.
PROBE: dict[str, Any] = {
    "result": {
        "model": "clairic-27b",
        "answers": {
            "due_date": {"type": "date", "p_null": 0.0048, "guess": "2026-10-12",
                         "confidence": 0.09602337458837694, "value": "2026-10-12"},
            "contacts": {"type": "list", "value": ["Dana Ruiz", "Priya Shah"],
                         "confidences": [0.9972, 0.9845], "confidence": 0.9816039576155414,
                         "valid": True, "complete": True, "spans": [[120, 129], [131, 141]]},
            "po_number": {"type": "text", "value": None, "valid": True, "complete": True,
                          "span": None, "p_null": 0.9996, "confidence": 0.9996129580899547},
            "total": {"type": "number", "value": 1080, "valid": True, "complete": True,
                      "span": None, "confidence": 0.9974057644829969},
            "invoice_number": {"type": "text", "value": "INV-2291", "valid": True, "complete": True,
                               "span": [8, 16], "confidence": 0.9999821188192167},
        },
        "truncated": False,
        "usage": {"state_tokens": 61, "image_tokens": 0, "schema_tokens": 1809,
                  "input_tokens": 2541, "output_tokens": 41, "passes": 9},
    },
    "success": True, "errors": [], "messages": [],
}

INVOICE_FIELDS = (
    FieldSpec("invoice_number", FieldType.TEXT),
    FieldSpec("due_date", FieldType.DATE, instructions="Issue date plus the net payment days"),
    FieldSpec("total", FieldType.NUMBER, decimals=2, unit="USD", instructions="Amount due including shipping"),
    FieldSpec("contacts", FieldType.TEXT_LIST),
    FieldSpec("po_number", FieldType.TEXT, instructions="Purchase order number"),
)


def _request(text: str = SAMPLE, fields: tuple[FieldSpec, ...] = INVOICE_FIELDS) -> ExtractionRequest:
    return ExtractionRequest(text=text, fields=fields)


def _ok(payload: Any = None, status: int = 200, **kw: Any) -> httpx.Response:
    return httpx.Response(status, json=PROBE if payload is None else payload, **kw)


def _extractor(
    handler: Callable[[httpx.Request], httpx.Response], **kw: Any
) -> tuple[ClairicExtractor, list[httpx.Request], FrozenClock]:
    seen: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    clock = kw.pop("clock", None) or FrozenClock()
    ext = ClairicExtractor(
        account_id=ACCOUNT, api_key=KEY, clock=clock, transport=httpx.MockTransport(record), **kw
    )
    return ext, seen, clock


# ---------------------------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------------------------

LABELLED = """Invoice number: INV-2291
Subtotal: 4
Total: $1,080.00
Due date: 2026-10-02
Contacts: Dana Ruiz, Priya Shah
Paid: yes
Status: OPEN
"""


class TestInMemoryExtractor:
    def _specs(self) -> tuple[FieldSpec, ...]:
        return (
            FieldSpec("invoice_number", FieldType.TEXT),
            FieldSpec("total", FieldType.NUMBER),
            FieldSpec("due_date", FieldType.DATE),
            FieldSpec("contacts", FieldType.TEXT_LIST),
            FieldSpec("paid", FieldType.BOOLEAN),
            FieldSpec("status", FieldType.ENUM, enum_values=("open", "paid")),
            FieldSpec("po_number", FieldType.TEXT),
        )

    def test_it_satisfies_the_port(self) -> None:
        assert isinstance(InMemoryStructuredExtractor(), StructuredExtractor)

    async def test_it_reads_labelled_lines_into_typed_values(self) -> None:
        got = await InMemoryStructuredExtractor().extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        f = got.fields
        assert f["invoice_number"].value == "INV-2291"
        start = LABELLED.index("INV-2291")
        assert f["invoice_number"].span == (start, start + len("INV-2291"))
        assert f["total"].value == 1080, "a label must not match the end of another word ('Subtotal')"
        assert f["due_date"].value == "2026-10-02"
        assert f["contacts"].value == ["Dana Ruiz", "Priya Shah"]
        assert f["paid"].value is True
        assert f["status"].value == "open", "an enum answer is returned in the declared spelling"

    async def test_a_field_the_text_does_not_state_is_a_confident_null(self) -> None:
        got = await InMemoryStructuredExtractor().extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        assert got.fields["po_number"].status is FieldStatus.NULL
        assert got.fields["po_number"].confidence == pytest.approx(0.99)

    async def test_the_answers_come_back_in_the_requests_order(self) -> None:
        got = await InMemoryStructuredExtractor().extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        assert list(got.fields) == [s.name for s in self._specs()]

    async def test_a_fixed_low_confidence_answer_is_withheld_by_the_real_rule(self) -> None:
        ext = InMemoryStructuredExtractor(fixed={"due_date": ("2026-10-12", 0.096)})
        got = await ext.extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        due = got.fields["due_date"]
        assert due.status is FieldStatus.LOW_CONFIDENCE and due.value is None and due.guess == "2026-10-12"

    async def test_text_that_does_not_fit_the_type_is_invalid_not_coerced(self) -> None:
        got = await InMemoryStructuredExtractor().extract(
            _request("Total: lots\nPaid: maybe\nDue date: soon", self._specs()[1:5]), min_confidence=0.5
        )
        assert got.fields["total"].status is FieldStatus.INVALID
        assert got.fields["paid"].status is FieldStatus.INVALID
        assert got.fields["due_date"].status is FieldStatus.INVALID

    async def test_field_names_match_labels_with_any_separator(self) -> None:
        specs = (FieldSpec("po.number", FieldType.TEXT), FieldSpec("due-date", FieldType.DATE))
        got = await InMemoryStructuredExtractor().extract(
            _request("PO number: 77\nDue date: 2026-01-31", specs), min_confidence=0.5
        )
        assert got.fields["po.number"].value == "77" and got.fields["due-date"].value == "2026-01-31"

    async def test_it_records_sizes_never_text(self) -> None:
        ext = InMemoryStructuredExtractor()
        await ext.extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        assert ext.calls == [len(LABELLED)]

    async def test_fail_next_raises_once(self) -> None:
        ext = InMemoryStructuredExtractor()
        ext.fail_next = ExtractionError("upstream down")
        with pytest.raises(ExtractionError):
            await ext.extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        assert ext.calls == []
        await ext.extract(_request(LABELLED, self._specs()), min_confidence=0.5)
        assert ext.calls == [len(LABELLED)]


# ---------------------------------------------------------------------------------------------
# Cloudflare clairic: what is sent
# ---------------------------------------------------------------------------------------------


class TestClairicRequest:
    async def test_it_posts_to_the_run_path_with_the_bearer_and_a_minimal_body(self) -> None:
        ext, seen, _ = _extractor(lambda r: _ok())
        await ext.extract(_request(), min_confidence=0.5)
        (req,) = seen
        assert req.method == "POST" and str(req.url) == RUN_URL
        assert req.headers["authorization"] == f"Bearer {KEY}"
        body = json.loads(req.content)
        assert set(body) == {"model", "state", "fields"}
        assert body["model"] == "clairic" and body["state"] == SAMPLE
        assert body["fields"] == {
            "invoice_number": {"type": "text"},
            "due_date": {"type": "date", "instructions": "Issue date plus the net payment days"},
            "total": {"type": "number", "decimals": 2, "unit": "USD", "instructions": "Amount due including shipping"},
            "contacts": {"type": "list", "items": "text"},
            "po_number": {"type": "text", "instructions": "Purchase order number"},
        }

    async def test_enum_and_integer_options_are_sent(self) -> None:
        specs = (
            FieldSpec("status", FieldType.ENUM, enum_values=("open", "paid")),
            FieldSpec("pallets", FieldType.NUMBER, integer=True),
            FieldSpec("rush", FieldType.BOOLEAN),
        )
        ext, seen, _ = _extractor(lambda r: _ok({"success": True, "result": {"answers": {}}}))
        await ext.extract(_request(fields=specs), min_confidence=0.5)
        assert json.loads(seen[0].content)["fields"] == {
            "status": {"type": "enum", "enum": ["open", "paid"]},
            "pallets": {"type": "number", "integer": True},
            "rush": {"type": "boolean"},
        }

    async def test_a_redirect_is_never_followed(self) -> None:
        """The credential must not follow a ``Location`` header anywhere."""
        ext, seen, _ = _extractor(lambda r: httpx.Response(302, headers={"location": "https://evil.example/x"}))
        with pytest.raises(ExtractionError) as caught:
            await ext.extract(_request(), min_confidence=0.5)
        assert len(seen) == 1 and caught.value.details["status"] == 302 and not caught.value.retryable

    def test_the_url_is_built_from_the_registry_not_from_settings(self) -> None:
        assert is_hosted_open_weight_host(EXTRACTION_API_HOST), "the host stays in the gated registry"
        assert is_external_inference_host(EXTRACTION_API_HOST) is None
        assert EXTRACTION_MODEL == "@cf/cloudflare/clairic"


# ---------------------------------------------------------------------------------------------
# Cloudflare clairic: what comes back
# ---------------------------------------------------------------------------------------------


class TestClairicResponse:
    async def test_the_recorded_probe_is_read_and_the_wrong_date_is_withheld(self) -> None:
        ext, _, _ = _extractor(lambda r: _ok())
        got = await ext.extract(_request(), min_confidence=0.5)
        f = got.fields
        assert (f["invoice_number"].status, f["invoice_number"].value, f["invoice_number"].span) == (
            FieldStatus.OK, "INV-2291", (8, 16))
        assert (f["total"].status, f["total"].value) == (FieldStatus.OK, 1080)
        assert (f["contacts"].status, f["contacts"].value) == (FieldStatus.OK, ["Dana Ruiz", "Priya Shah"])
        assert f["po_number"].status is FieldStatus.NULL
        assert f["due_date"].status is FieldStatus.LOW_CONFIDENCE
        assert f["due_date"].value is None and f["due_date"].guess == "2026-10-12"
        assert got.model == "clairic-27b" and not got.truncated
        assert (got.input_tokens, got.output_tokens) == (2541, 41)
        assert list(f) == [s.name for s in INVOICE_FIELDS]

    async def test_a_lower_threshold_lets_the_same_answer_through(self) -> None:
        """The threshold is the caller's decision, not something the adapter hides."""
        ext, _, _ = _extractor(lambda r: _ok())
        got = await ext.extract(_request(), min_confidence=0.05)
        assert got.fields["due_date"].status is FieldStatus.OK and got.fields["due_date"].value == "2026-10-12"

    async def test_a_missing_answer_is_invalid_not_null(self) -> None:
        payload = json.loads(json.dumps(PROBE))
        del payload["result"]["answers"]["total"]
        ext, _, _ = _extractor(lambda r: _ok(payload))
        got = await ext.extract(_request(), min_confidence=0.5)
        assert got.fields["total"].status is FieldStatus.INVALID and got.fields["total"].reason == "invalid_output"

    @pytest.mark.parametrize("flag", [{"valid": False}, {"complete": False}])
    async def test_an_answer_the_service_marks_unusable_is_invalid(self, flag: dict) -> None:
        payload = json.loads(json.dumps(PROBE))
        payload["result"]["answers"]["total"].update(flag)
        ext, _, _ = _extractor(lambda r: _ok(payload))
        got = await ext.extract(_request(), min_confidence=0.5)
        assert got.fields["total"].status is FieldStatus.INVALID and got.fields["total"].value is None

    async def test_a_wrong_type_from_upstream_is_invalid(self) -> None:
        payload = json.loads(json.dumps(PROBE))
        payload["result"]["answers"]["total"]["value"] = "1080"
        ext, _, _ = _extractor(lambda r: _ok(payload))
        got = await ext.extract(_request(), min_confidence=0.5)
        assert got.fields["total"].status is FieldStatus.INVALID and got.fields["total"].reason == "type_mismatch"

    async def test_truncation_is_reported(self) -> None:
        payload = json.loads(json.dumps(PROBE))
        payload["result"]["truncated"] = True
        ext, _, _ = _extractor(lambda r: _ok(payload))
        assert (await ext.extract(_request(), min_confidence=0.5)).truncated is True

    @pytest.mark.parametrize(
        "response",
        [
            lambda: httpx.Response(200, content=b"<html>not json</html>"),
            lambda: _ok({"success": False, "result": {"answers": {}}}),
            lambda: _ok({"success": True, "result": None}),
            lambda: _ok({"success": True, "result": {"answers": []}}),
            lambda: _ok([1, 2, 3]),
        ],
    )
    async def test_an_unreadable_response_is_an_error_not_an_empty_result(self, response: Callable) -> None:
        ext, _, _ = _extractor(lambda r: response())
        with pytest.raises(ExtractionError):
            await ext.extract(_request(), min_confidence=0.5)

    async def test_an_oversize_response_is_refused_unread(self) -> None:
        ext, _, _ = _extractor(lambda r: httpx.Response(200, content=b"x" * 1_000_001))
        with pytest.raises(ExtractionError) as caught:
            await ext.extract(_request(), min_confidence=0.5)
        assert not caught.value.retryable


# ---------------------------------------------------------------------------------------------
# Cloudflare clairic: failure, cooldown and secrecy
# ---------------------------------------------------------------------------------------------


class TestClairicFailures:
    async def test_a_429_opens_a_cooldown_that_spares_the_network(self) -> None:
        responses = iter([httpx.Response(429, headers={"retry-after": "7"}), _ok()])
        ext, seen, clock = _extractor(lambda r: next(responses))
        with pytest.raises(RateLimitedError) as first:
            await ext.extract(_request(), min_confidence=0.5)
        assert first.value.retry_after == 7 and len(seen) == 1
        with pytest.raises(RateLimitedError) as second:
            await ext.extract(_request(), min_confidence=0.5)
        assert second.value.retry_after == 7 and len(seen) == 1, "no request during the cooldown"
        clock.advance(8)
        assert (await ext.extract(_request(), min_confidence=0.5)).fields and len(seen) == 2

    @pytest.mark.parametrize(
        "header, expected",
        [({}, 60), ({"retry-after": "abc"}, 60), ({"retry-after": "99999"}, 3600),
         ({"retry-after": "0"}, 1), ({"retry-after": "2.5"}, 2)],
    )
    async def test_retry_after_is_clamped_and_defaulted(self, header: dict, expected: int) -> None:
        ext, _, _ = _extractor(lambda r: httpx.Response(429, headers=header))
        with pytest.raises(RateLimitedError) as caught:
            await ext.extract(_request(), min_confidence=0.5)
        assert caught.value.retry_after == expected

    async def test_a_5xx_is_retryable_and_cools_down_briefly(self) -> None:
        body = {"errors": [{"code": 1234, "message": "SECRET-ECHO invoice text"}]}
        responses = iter([httpx.Response(503, json=body), _ok()])
        ext, seen, clock = _extractor(lambda r: next(responses))
        with pytest.raises(ExtractionError) as caught:
            await ext.extract(_request(), min_confidence=0.5)
        err = caught.value
        assert err.retryable and err.details == {"status": 503, "codes": [1234]}
        assert "SECRET-ECHO" not in str(err) and "SECRET-ECHO" not in str(err.to_dict())
        with pytest.raises(RateLimitedError) as cooling:
            await ext.extract(_request(), min_confidence=0.5)
        assert cooling.value.retry_after == 10 and len(seen) == 1
        clock.advance(11)
        await ext.extract(_request(), min_confidence=0.5)
        assert len(seen) == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    async def test_a_client_error_is_not_retryable_and_opens_no_cooldown(self, status: int) -> None:
        ext, seen, _ = _extractor(lambda r: httpx.Response(status, json={"errors": [{"code": 10000, "message": "x"}]}))
        for _ in range(2):
            with pytest.raises(ExtractionError) as caught:
                await ext.extract(_request(), min_confidence=0.5)
            assert not caught.value.retryable and caught.value.details["status"] == status
        assert len(seen) == 2, "a refusal is the same next time, but it does not block other calls"

    @pytest.mark.parametrize("exc", [httpx.ConnectError, httpx.ReadTimeout])
    async def test_a_transport_failure_never_carries_the_request_or_the_credential(self, exc: type) -> None:
        def boom(request: httpx.Request) -> httpx.Response:
            raise exc(f"boom Authorization: Bearer {KEY} {SAMPLE}", request=request)

        ext, _, _ = _extractor(boom)
        with pytest.raises(ExtractionError) as caught:
            await ext.extract(_request(), min_confidence=0.5)
        err = caught.value
        assert err.retryable and err.__cause__ is None
        everything = f"{err} {err.to_dict()} {err.details}"
        assert KEY not in everything and "Northwind" not in everything
        assert exc.__name__ in str(err)
        with pytest.raises(RateLimitedError):
            await ext.extract(_request(), min_confidence=0.5)

    async def test_the_credential_is_in_no_repr_and_no_error(self) -> None:
        ext, _, _ = _extractor(lambda r: _ok())
        assert KEY not in repr(ext) and KEY not in str(vars(ext).get("_url", ""))


class TestClairicLifecycle:
    @pytest.mark.parametrize("account", ["", "abc", "G" * 32, "0123456789ABCDEF0123456789ABCDEF",
                                         "../../etc/passwd", ACCOUNT + "/x", ACCOUNT[:-1],
                                         ACCOUNT + "\n"])
    def test_a_bad_account_id_is_refused_because_it_is_part_of_a_url(self, account: str) -> None:
        with pytest.raises(ConfigurationError):
            ClairicExtractor(account_id=account, api_key=KEY, clock=FrozenClock())

    @pytest.mark.parametrize("key", ["", "   ", None])
    def test_an_empty_credential_is_refused(self, key: Any) -> None:
        with pytest.raises(ConfigurationError) as caught:
            ClairicExtractor(account_id=ACCOUNT, api_key=key, clock=FrozenClock())
        assert "AURA_EXTRACT_API_KEY_ENV" in str(caught.value)

    @pytest.mark.parametrize("timeout", [0, -1, 301, "5", None])
    def test_a_bad_timeout_is_refused(self, timeout: Any) -> None:
        with pytest.raises(ConfigurationError):
            ClairicExtractor(account_id=ACCOUNT, api_key=KEY, clock=FrozenClock(), timeout_s=timeout)

    def test_it_satisfies_the_port(self) -> None:
        ext, _, _ = _extractor(lambda r: _ok())
        assert isinstance(ext, StructuredExtractor)
        assert (ext.name, ext.model_id) == ("clairic", EXTRACTION_MODEL)

    async def test_health_reports_cooldown_and_never_calls_out(self) -> None:
        ext, seen, clock = _extractor(lambda r: httpx.Response(429, headers={"retry-after": "30"}))
        assert (await ext.health()).healthy
        with pytest.raises(RateLimitedError):
            await ext.extract(_request(), min_confidence=0.5)
        status = await ext.health()
        assert not status.healthy and "30" in status.detail
        clock.advance(31)
        assert (await ext.health()).healthy
        assert len(seen) == 1, "health must not spend quota"

    async def test_close_is_idempotent_and_the_extractor_still_works_after(self) -> None:
        ext, seen, _ = _extractor(lambda r: _ok())
        await ext.extract(_request(), min_confidence=0.5)
        await ext.close()
        await ext.close()
        await ext.extract(_request(), min_confidence=0.5)
        assert len(seen) == 2

    def test_the_model_is_not_an_admitted_open_weight_family(self) -> None:
        """The premise of ADR-038's exception. If this starts failing, Cloudflare has said the
        weights are published and ADR-030's list was widened: retire the exception (ADR-038 O-2)."""
        assert not is_open_weight_model(EXTRACTION_MODEL)
