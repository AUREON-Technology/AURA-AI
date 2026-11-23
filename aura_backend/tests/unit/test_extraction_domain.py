"""Domain rules for typed field extraction (ADR-038).

Pure logic: no network, no adapter. The numbers in ``TestClassifyAnswer`` are the ones the real
service returned in the first probe (2026-11-22), including the wrong due date at confidence 0.096.
"""

from __future__ import annotations

import math

import pytest

from aura_core.domain.errors import (
    AuraError,
    BudgetExceededError,
    ExtractionError,
    ExtractionRefusedError,
    RateLimitedError,
    ValidationError,
)
from aura_core.domain.extraction import (
    MAX_ENUM_VALUES,
    MAX_FIELDS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_LIST_ITEMS,
    MAX_TEXT_VALUE_CHARS,
    ExtractedField,
    ExtractionRequest,
    ExtractionResult,
    FieldSpec,
    FieldStatus,
    FieldType,
    check_value,
    classify_answer,
)
from aura_core.ports import HealthStatus, StructuredExtractor


def _spec(kind: str, **kw) -> FieldSpec:
    return FieldSpec(name="f", type=FieldType(kind), **kw)


class TestFieldSpec:
    def test_the_probe_fields_are_accepted(self) -> None:
        FieldSpec.from_mapping("invoice_number", {"type": "text"})
        FieldSpec.from_mapping("due_date", {"type": "date", "instructions": "Issue date plus net days"})
        total = FieldSpec.from_mapping("total", {"type": "number", "decimals": 2, "unit": "USD"})
        assert total.type is FieldType.NUMBER and total.decimals == 2
        FieldSpec.from_mapping("contacts", {"type": "text_list"})

    @pytest.mark.parametrize("name", ["", "1abc", "has space", "a" * 65, "x/y", "naïve"])
    def test_a_bad_name_is_refused(self, name: str) -> None:
        with pytest.raises(ValidationError):
            FieldSpec(name=name, type=FieldType.TEXT)

    @pytest.mark.parametrize("name", ["a", "_x", "po.number", "due-date", "A1_b.c-d", "z" * 64])
    def test_a_service_legal_name_is_accepted(self, name: str) -> None:
        FieldSpec(name=name, type=FieldType.TEXT)

    @pytest.mark.parametrize("kind", ["estimate", "forecast", "compute", "list", "", "TEXT "])
    def test_types_v1_does_not_offer_are_refused(self, kind: str) -> None:
        with pytest.raises(ValidationError) as caught:
            FieldSpec(name="f", type=kind)  # type: ignore[arg-type]
        assert "supported" in caught.value.details

    def test_long_instructions_are_refused_not_trimmed(self) -> None:
        FieldSpec(name="f", type=FieldType.TEXT, instructions="x" * MAX_INSTRUCTIONS_CHARS)
        with pytest.raises(ValidationError):
            FieldSpec(name="f", type=FieldType.TEXT, instructions="x" * (MAX_INSTRUCTIONS_CHARS + 1))

    def test_an_enum_needs_distinct_values(self) -> None:
        FieldSpec(name="f", type=FieldType.ENUM, enum_values=("a", "b"))
        for bad in [(), ("a", "a"), ("a", ""), tuple(str(i) for i in range(MAX_ENUM_VALUES + 1))]:
            with pytest.raises(ValidationError):
                FieldSpec(name="f", type=FieldType.ENUM, enum_values=bad)

    def test_only_an_enum_takes_enum_values(self) -> None:
        with pytest.raises(ValidationError):
            FieldSpec(name="f", type=FieldType.TEXT, enum_values=("a",))

    @pytest.mark.parametrize("kw", [{"integer": True}, {"decimals": 2}, {"unit": "USD"}])
    def test_number_options_are_for_numbers_only(self, kw: dict) -> None:
        with pytest.raises(ValidationError):
            FieldSpec(name="f", type=FieldType.TEXT, **kw)

    @pytest.mark.parametrize(
        "kw", [{"decimals": 7}, {"decimals": -1}, {"decimals": True}, {"decimals": 2, "integer": True}]
    )
    def test_bad_number_options_are_refused(self, kw: dict) -> None:
        with pytest.raises(ValidationError):
            FieldSpec(name="f", type=FieldType.NUMBER, **kw)

    def test_from_mapping_refuses_unknown_keys_rather_than_ignoring_them(self) -> None:
        with pytest.raises(ValidationError) as caught:
            FieldSpec.from_mapping("f", {"type": "text", "instruction": "typo"})
        assert "instruction" in caught.value.message

    def test_from_mapping_needs_a_type_and_an_object(self) -> None:
        with pytest.raises(ValidationError):
            FieldSpec.from_mapping("f", {})
        with pytest.raises(ValidationError):
            FieldSpec.from_mapping("f", "text")  # type: ignore[arg-type]
        with pytest.raises(ValidationError):
            FieldSpec.from_mapping("f", {"type": "enum", "enum": "a,b"})


class TestExtractionRequest:
    def _fields(self, n: int) -> tuple[FieldSpec, ...]:
        return tuple(FieldSpec(name=f"f{i}", type=FieldType.TEXT) for i in range(n))

    def test_a_valid_request_keeps_its_field_order(self) -> None:
        req = ExtractionRequest(text="Invoice INV-1", fields=self._fields(3))
        assert [s.name for s in req.fields] == ["f0", "f1", "f2"]
        assert req.character_count == len("Invoice INV-1")
        assert req.spec_for("f1").name == "f1"
        with pytest.raises(KeyError):
            req.spec_for("missing")

    @pytest.mark.parametrize("text", ["", "   ", "\n\t"])
    def test_blank_text_is_refused(self, text: str) -> None:
        with pytest.raises(ValidationError):
            ExtractionRequest(text=text, fields=self._fields(1))

    def test_between_one_and_the_maximum_fields(self) -> None:
        ExtractionRequest(text="x", fields=self._fields(MAX_FIELDS))
        for n in (0, MAX_FIELDS + 1):
            with pytest.raises(ValidationError):
                ExtractionRequest(text="x", fields=self._fields(n))

    def test_field_names_must_be_unique(self) -> None:
        dup = (FieldSpec(name="a", type=FieldType.TEXT), FieldSpec(name="a", type=FieldType.DATE))
        with pytest.raises(ValidationError):
            ExtractionRequest(text="x", fields=dup)

    def test_a_list_of_fields_is_stored_as_a_tuple(self) -> None:
        req = ExtractionRequest(text="x", fields=list(self._fields(2)))  # type: ignore[arg-type]
        assert isinstance(req.fields, tuple)


class TestCheckValue:
    @pytest.mark.parametrize(
        "spec, value, reason",
        [
            (_spec("text"), "INV-2291", None),
            (_spec("text"), 5, "type_mismatch"),
            (_spec("text"), "x" * (MAX_TEXT_VALUE_CHARS + 1), "too_long"),
            (_spec("number"), 1080, None),
            (_spec("number"), 1080.5, None),
            (_spec("number"), "1080", "type_mismatch"),
            (_spec("number"), True, "type_mismatch"),
            (_spec("number"), math.nan, "not_finite"),
            (_spec("number"), math.inf, "not_finite"),
            (_spec("number", integer=True), 4, None),
            (_spec("number", integer=True), 4.5, "not_integer"),
            (_spec("date"), "2026-10-02", None),
            (_spec("date"), "2026-02-30", "not_a_date"),
            (_spec("date"), "20261002", "not_a_date"),
            (_spec("date"), "2026-W40-5", "not_a_date"),
            (_spec("date"), "02/10/2026", "not_a_date"),
            (_spec("date"), 20261002, "not_a_date"),
            (_spec("boolean"), True, None),
            (_spec("boolean"), "true", "type_mismatch"),
            (_spec("boolean"), 1, "type_mismatch"),
            (_spec("enum", enum_values=("paid", "open")), "paid", None),
            (_spec("enum", enum_values=("paid", "open")), "late", "not_in_enum"),
            (_spec("enum", enum_values=("paid", "open")), 3, "type_mismatch"),
            (_spec("text_list"), ["Dana Ruiz", "Priya Shah"], None),
            (_spec("text_list"), [], None),
            (_spec("text_list"), ["a", 1], "type_mismatch"),
            (_spec("text_list"), "Dana Ruiz", "type_mismatch"),
            (_spec("text_list"), ["a"] * (MAX_LIST_ITEMS + 1), "too_many_items"),
            (_spec("text_list"), ["x" * (MAX_TEXT_VALUE_CHARS + 1)], "too_long"),
        ],
    )
    def test_each_type_rule(self, spec: FieldSpec, value: object, reason: str | None) -> None:
        assert check_value(spec, value) == reason


class TestClassifyAnswer:
    """The withholding rule. ``min_confidence`` is 0.5 throughout unless a test says otherwise."""

    def test_the_wrong_due_date_from_the_probe_is_withheld(self) -> None:
        got = classify_answer(
            _spec("date"), value="2026-10-12", confidence=0.09602337458837694, min_confidence=0.5
        )
        assert got.status is FieldStatus.LOW_CONFIDENCE
        assert got.value is None, "a low-confidence value must never reach the caller as an answer"
        assert got.guess == "2026-10-12"
        assert got.reason == "below_threshold"

    def test_the_confident_probe_answers_are_returned(self) -> None:
        total = classify_answer(
            _spec("number", decimals=2), value=1080, confidence=0.9974057644829969, min_confidence=0.5
        )
        number = classify_answer(
            _spec("text"), value="INV-2291", confidence=0.9999821188192167, min_confidence=0.5,
            span=[8, 16],
        )
        assert (total.status, total.value) == (FieldStatus.OK, 1080)
        assert (number.status, number.value, number.span) == (FieldStatus.OK, "INV-2291", (8, 16))

    def test_a_null_stays_null_and_keeps_its_confidence(self) -> None:
        got = classify_answer(_spec("text"), value=None, confidence=0.9996129580899547, min_confidence=0.5)
        assert got.status is FieldStatus.NULL and got.value is None
        assert got.confidence == pytest.approx(0.9996, abs=1e-4)

    def test_a_confidence_exactly_at_the_threshold_is_accepted(self) -> None:
        assert classify_answer(_spec("text"), value="x", confidence=0.5, min_confidence=0.5).status is FieldStatus.OK
        assert (
            classify_answer(_spec("text"), value="x", confidence=0.4999, min_confidence=0.5).status
            is FieldStatus.LOW_CONFIDENCE
        )

    @pytest.mark.parametrize("confidence", [None, math.nan, math.inf, -0.1, 1.1, "0.9", True])
    def test_an_unverifiable_confidence_counts_as_low(self, confidence: object) -> None:
        got = classify_answer(_spec("text"), value="x", confidence=confidence, min_confidence=0.0)
        assert got.status is FieldStatus.LOW_CONFIDENCE and got.reason == "no_confidence"
        assert got.value is None and got.guess == "x"

    def test_an_invalid_answer_is_invalid_whatever_its_confidence(self) -> None:
        got = classify_answer(_spec("text"), value="secret-ish", confidence=0.99, min_confidence=0.5, valid=False)
        assert got.status is FieldStatus.INVALID and got.reason == "invalid_output"
        assert got.value is None and got.guess is None

    def test_a_value_of_the_wrong_type_is_invalid_and_not_echoed(self) -> None:
        got = classify_answer(_spec("number"), value="1080", confidence=0.99, min_confidence=0.5)
        assert got.status is FieldStatus.INVALID and got.reason == "type_mismatch"
        assert got.value is None and got.guess is None

    def test_a_bad_span_is_dropped_not_trusted(self) -> None:
        for span in ([5, 2], [-1, 3], [1], "ab", [True, 3], None):
            got = classify_answer(_spec("text"), value="x", confidence=0.9, min_confidence=0.5, span=span)
            assert got.span is None, span

    @pytest.mark.parametrize("bad", [-0.01, 1.01, math.nan, None, "0.5", True])
    def test_a_bad_threshold_is_a_caller_error(self, bad: object) -> None:
        with pytest.raises(ValidationError):
            classify_answer(_spec("text"), value="x", confidence=0.9, min_confidence=bad)  # type: ignore[arg-type]

    def test_a_threshold_of_zero_still_withholds_an_answer_with_no_confidence(self) -> None:
        got = classify_answer(_spec("text"), value="x", confidence=None, min_confidence=0.0)
        assert got.status is FieldStatus.LOW_CONFIDENCE


class TestResultShape:
    def _result(self) -> ExtractionResult:
        spec_d, spec_t = _spec("date"), FieldSpec(name="invoice_number", type=FieldType.TEXT)
        return ExtractionResult(
            fields={
                "due_date": classify_answer(spec_d, value="2026-10-12", confidence=0.096, min_confidence=0.5),
                "invoice_number": classify_answer(
                    spec_t, value="INV-2291", confidence=0.99998, min_confidence=0.5, span=(8, 16)
                ),
            },
            model="clairic-27b", input_tokens=2541, output_tokens=41,
        )

    def test_only_a_low_confidence_field_carries_its_guess(self) -> None:
        answers = self._result().as_dict()["answers"]
        assert answers["due_date"]["value"] is None and answers["due_date"]["guess"] == "2026-10-12"
        assert "guess" not in answers["invoice_number"]
        assert answers["invoice_number"]["span"] == [8, 16]
        assert answers["invoice_number"]["confidence"] == 1.0

    def test_usage_and_model_are_reported(self) -> None:
        out = self._result().as_dict()
        assert out["model"] == "clairic-27b" and out["truncated"] is False
        assert out["usage"] == {"input_tokens": 2541, "output_tokens": 41}

    def test_the_fields_mapping_cannot_be_changed_afterwards(self) -> None:
        result = self._result()
        with pytest.raises(TypeError):
            result.fields["x"] = ExtractedField("x", FieldStatus.NULL)  # type: ignore[index]

    def test_the_result_is_json_serialisable(self) -> None:
        import json

        json.dumps(self._result().as_dict())


class TestErrors:
    def test_a_refusal_is_an_extraction_error_that_is_not_retryable(self) -> None:
        err = ExtractionRefusedError("above the internal data scope", reason="data_ceiling")
        assert isinstance(err, ExtractionError) and isinstance(err, AuraError)
        assert (err.kind, err.http_status, err.retryable) == ("extraction_refused", 403, False)
        assert err.reason == "data_ceiling" and err.details["reason"] == "data_ceiling"

    def test_an_upstream_failure_is_retryable(self) -> None:
        err = ExtractionError("upstream returned 503", details={"status": 503})
        assert (err.kind, err.http_status, err.retryable) == ("extraction_error", 502, True)

    def test_the_serialised_form_does_not_carry_the_cause(self) -> None:
        err = ExtractionError("failed", cause=RuntimeError("Bearer cfat_not_a_real_token"))
        assert "cfat_" not in str(err.to_dict())

    def test_cap_and_cooldown_reuse_the_gateways_existing_errors(self) -> None:
        assert BudgetExceededError.http_status == 429 and RateLimitedError.http_status == 429


class TestPort:
    def test_a_structural_implementation_satisfies_the_port(self) -> None:
        class Fake:
            name = "fake"
            model_id = "fake-1"

            async def extract(self, request, *, min_confidence):  # noqa: ANN001
                raise NotImplementedError

            async def health(self) -> HealthStatus:
                return HealthStatus(True)

            async def close(self) -> None:
                return None

        assert isinstance(Fake(), StructuredExtractor)

    def test_something_missing_a_method_does_not(self) -> None:
        class NotOne:
            name = "n"
            model_id = "n"

        assert not isinstance(NotOne(), StructuredExtractor)
