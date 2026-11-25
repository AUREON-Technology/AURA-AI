"""The ``extract_structured`` tool (ADR-038): its gates, its audit record, and what it will not say.

Runs the real tool over the in-memory extractor and audit sink, with the real data scope. The scope
rules mirror the kernel's: a turn starts ``internal``, and anything private -- or any tool output,
which the registry escalates on -- raises it to ``sensitive``.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from aura_core.adapters.memory_extractor import InMemoryStructuredExtractor
from aura_core.adapters.runtime import FrozenClock, InMemoryAuditSink
from aura_core.domain.errors import (
    BudgetExceededError,
    ExtractionError,
    RateLimitedError,
    ToolExecutionError,
)
from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import DataClassification
from aura_core.llm.sensitivity import data_scope, escalate
from aura_core.ports.runtime import ToolContext
from aura_core.tools.extract import (
    EXTRACT_AUDIT_ACTION,
    EXTRACT_TOOL_NAME,
    DailyCallCap,
    build_extract_tools,
)

TEXT = (
    "Invoice number: INV-2291\n"
    "Total: $1,080.00\n"
    "Due date: 2026-10-02\n"
    "Contacts: Dana Ruiz, Priya Shah\n"
    "Northwind-Secret-4471\n"
)
FIELDS = {
    "invoice_number": {"type": "text"},
    "total": {"type": "number", "decimals": 2, "unit": "USD"},
    "due_date": {"type": "date", "instructions": "the date payment is due"},
    "contacts": {"type": "text_list"},
    "po_number": {"type": "text"},
}
INTERNAL = DataClassification.INTERNAL
SENSITIVE = DataClassification.SENSITIVE


def _ctx() -> ToolContext:
    return ToolContext(user_id=UserId("user-1"), correlation_id="corr-1", deadline_s=30.0)


class _Rig:
    def __init__(self, **kw) -> None:
        self.extractor = kw.pop("extractor", None) or InMemoryStructuredExtractor()
        self.audit = kw.pop("audit", None) or InMemoryAuditSink()
        self.clock = kw.pop("clock", None) or FrozenClock()
        (self.tool,) = build_extract_tools(
            extractor=self.extractor, audit=self.audit, clock=self.clock, **kw
        )

    async def call(self, text: str = TEXT, fields=FIELDS) -> dict:
        return json.loads(await self.tool.execute({"text": text, "fields": fields}, _ctx()))

    async def events(self):
        return await self.audit.query(action=EXTRACT_AUDIT_ACTION)


class TestTheDeclaration:
    def test_it_is_one_read_only_tool_that_taints_the_turn(self) -> None:
        rig = _Rig()
        spec = rig.tool.spec
        assert spec.name == EXTRACT_TOOL_NAME == "extract_structured"
        assert not spec.side_effecting and not spec.required_capability and not spec.workspace_local
        assert "untrusted_content" in spec.tags

    def test_the_text_parameter_carries_the_configured_limit_and_the_timeout_leaves_room(self) -> None:
        rig = _Rig(max_chars=1234, timeout_s=20.0)
        text = next(p for p in rig.tool.spec.parameters if p.name == "text")
        assert text.max_length == 1234 and text.required
        assert rig.tool.spec.timeout_s == 25.0

    def test_the_description_tells_the_model_not_to_state_a_guess_as_fact(self) -> None:
        assert "never state a guess as a fact" in _Rig().tool.spec.description


class TestTheHappyPath:
    async def test_a_turn_holding_only_the_persons_words_is_served(self) -> None:
        rig = _Rig()
        with data_scope(INTERNAL):
            out = await rig.call()
        a = out["answers"]
        assert a["invoice_number"]["value"] == "INV-2291" and a["invoice_number"]["status"] == "ok"
        assert a["total"]["value"] == 1080 and a["due_date"]["value"] == "2026-10-02"
        assert a["contacts"]["value"] == ["Dana Ruiz", "Priya Shah"]
        assert a["po_number"]["status"] == "null"
        assert out["withheld"] == [] and "note" not in out and out["truncated"] is False
        assert rig.extractor.calls == [len(TEXT)]

    async def test_a_public_scope_is_below_the_ceiling_and_is_served(self) -> None:
        rig = _Rig()
        with data_scope(DataClassification.PUBLIC):
            assert (await rig.call())["answers"]["total"]["value"] == 1080

    async def test_the_configured_threshold_reaches_the_extractor(self) -> None:
        rig = _Rig(min_confidence=0.99)  # the in-memory reader is 0.95 confident
        with data_scope(INTERNAL):
            out = await rig.call()
        assert out["answers"]["invoice_number"]["status"] == "low_confidence"
        assert out["answers"]["invoice_number"]["value"] is None


class TestWithheldValues:
    async def test_a_low_confidence_value_is_withheld_and_named(self) -> None:
        rig = _Rig(extractor=InMemoryStructuredExtractor(fixed={"due_date": ("2026-10-12", 0.096)}))
        with data_scope(INTERNAL):
            out = await rig.call()
        due = out["answers"]["due_date"]
        assert due["status"] == "low_confidence" and due["value"] is None and due["guess"] == "2026-10-12"
        assert out["withheld"] == ["due_date"]
        assert "not a fact" in out["note"]
        assert out["answers"]["total"]["value"] == 1080, "the confident answers are unaffected"


class TestTheDataCeiling:
    @pytest.mark.parametrize("reason", ["memory", "documents", "pinned_documents", "goals",
                                        "session_history", "engineer_workspace", "caller_evidence",
                                        "image_input", "tool_output"])
    async def test_a_turn_that_drew_on_private_context_is_refused_before_anything_is_sent(self, reason) -> None:
        rig = _Rig()
        with data_scope(INTERNAL) as scope:
            scope.escalate(SENSITIVE, reason)
            with pytest.raises(ToolExecutionError) as caught:
                await rig.call()
        assert reason in caught.value.message and not caught.value.retryable
        assert rig.extractor.calls == [], "nothing may reach the service"
        (event,) = await rig.events()
        assert (event.outcome, event.detail["reason"]) == ("denied", "data_ceiling")

    async def test_a_sensitive_baseline_is_refused(self) -> None:
        rig = _Rig()
        with data_scope(SENSITIVE, "engineer_workspace"):
            with pytest.raises(ToolExecutionError):
                await rig.call()
        assert rig.extractor.calls == []

    async def test_with_no_scope_at_all_it_fails_closed(self) -> None:
        rig = _Rig()
        with pytest.raises(ToolExecutionError) as caught:
            await rig.call()
        assert "an earlier step" in caught.value.message and rig.extractor.calls == []

    async def test_a_second_call_in_the_same_turn_is_refused_because_the_registry_escalates(self) -> None:
        """The registry raises the scope to sensitive the moment any tool returns output."""
        rig = _Rig()
        with data_scope(INTERNAL):
            await rig.call()
            escalate(SENSITIVE, "tool_output")
            with pytest.raises(ToolExecutionError) as caught:
                await rig.call()
        assert "tool_output" in caught.value.message and rig.extractor.calls == [len(TEXT)]


class TestArguments:
    @pytest.mark.parametrize(
        "text, fields, fragment",
        [
            (123, FIELDS, "text must be a string"),
            ("   ", FIELDS, "text must be non-empty"),
            ("x" * 201, FIELDS, "the limit is 200"),
            (TEXT, {}, "fields must be an object"),
            (TEXT, ["a"], "fields must be an object"),
            (TEXT, {"f": {"type": "estimate"}}, "unsupported type"),
            (TEXT, {"f": {"type": "text", "instruction": "typo"}}, "unknown key"),
            (TEXT, {"f": {"type": "enum"}}, "enum needs"),
            (TEXT, {"bad name": {"type": "text"}}, "field name"),
            (TEXT, {f"f{i}": {"type": "text"} for i in range(21)}, "between 1 and 20"),
        ],
    )
    async def test_a_bad_call_is_refused_with_a_reason_and_costs_nothing(self, text, fields, fragment) -> None:
        rig = _Rig(max_chars=200)
        with data_scope(INTERNAL):
            with pytest.raises(ToolExecutionError) as caught:
                await rig.call(text=text, fields=fields)
        assert fragment in caught.value.message and not caught.value.retryable
        assert rig.extractor.calls == [] and await rig.events() == []


class TestTheDailyCap:
    async def test_the_cap_is_enforced_and_resets_at_the_next_utc_midnight(self) -> None:
        rig = _Rig(daily_call_cap=2)
        with data_scope(INTERNAL):
            await rig.call()
            await rig.call()
            with pytest.raises(ToolExecutionError) as caught:
                await rig.call()
            assert "2026-01-02T00:00:00+00:00" in caught.value.message
            assert rig.extractor.calls == [len(TEXT)] * 2
            rig.clock.advance(timedelta(days=1).total_seconds())
            await rig.call()
        outcomes = [(e.outcome, e.detail["reason"]) for e in reversed(await rig.events())]
        assert outcomes == [("success", "ok"), ("success", "ok"), ("denied", "daily_cap"), ("success", "ok")]

    async def test_refused_and_malformed_calls_do_not_spend_the_allowance(self) -> None:
        rig = _Rig(daily_call_cap=2)
        for _ in range(5):
            with data_scope(SENSITIVE, "memory"), pytest.raises(ToolExecutionError):
                await rig.call()
            with data_scope(INTERNAL), pytest.raises(ToolExecutionError):
                await rig.call(fields={})
        with data_scope(INTERNAL):
            await rig.call()
            await rig.call()

    def test_the_cap_object_directly(self) -> None:
        clock = FrozenClock()
        cap = DailyCallCap(1, clock)
        cap.acquire()
        with pytest.raises(BudgetExceededError) as caught:
            cap.acquire()
        assert caught.value.details["reason"] == "daily_cap" and caught.value.details["cap"] == 1
        with pytest.raises(ValueError):
            DailyCallCap(0, clock)


class TestUpstreamFailures:
    async def test_an_upstream_error_is_a_clean_tool_error_and_is_audited(self) -> None:
        rig = _Rig()
        rig.extractor.fail_next = ExtractionError("boom SECRET", details={"status": 503}, retryable=True)
        with data_scope(INTERNAL), pytest.raises(ToolExecutionError) as caught:
            await rig.call()
        assert "HTTP 503" in caught.value.message and "SECRET" not in caught.value.message
        assert caught.value.retryable
        (event,) = await rig.events()
        assert (event.outcome, event.detail["reason"]) == ("failure", "upstream_error")

    async def test_a_rate_limit_names_the_wait(self) -> None:
        rig = _Rig()
        rig.extractor.fail_next = RateLimitedError("cooling", retry_after=9)
        with data_scope(INTERNAL), pytest.raises(ToolExecutionError) as caught:
            await rig.call()
        assert "9s" in caught.value.message and caught.value.retryable
        assert (await rig.events())[0].detail["reason"] == "rate_limited"

    async def test_a_non_retryable_upstream_error_says_so(self) -> None:
        rig = _Rig()
        rig.extractor.fail_next = ExtractionError("refused", details={"status": 401}, retryable=False)
        with data_scope(INTERNAL), pytest.raises(ToolExecutionError) as caught:
            await rig.call()
        assert not caught.value.retryable

    async def test_an_unexpected_exception_still_surfaces_as_a_tool_error(self) -> None:
        rig = _Rig()
        rig.extractor.fail_next = RuntimeError("bug")  # type: ignore[assignment]
        with data_scope(INTERNAL), pytest.raises(ToolExecutionError):
            await rig.call()


class TestTheAuditRecord:
    async def test_a_success_records_who_what_and_counts_but_never_content(self) -> None:
        rig = _Rig()
        with data_scope(INTERNAL):
            await rig.call()
        (event,) = await rig.events()
        assert (event.actor, event.outcome, event.resource, event.correlation_id) == (
            "user-1", "success", "memory", "corr-1")
        d = event.detail
        assert d["model"] == "memory-extractor-1" and d["fields"] == 5 and d["input_chars"] == len(TEXT)
        assert d["statuses"] == {"ok": 4, "null": 1} and d["data_classification"] == "internal"
        everything = repr({s: getattr(event, s, None) for s in event.__slots__})
        for secret in ("Northwind-Secret-4471", "INV-2291", "Dana Ruiz", "1080", "2026-10-02"):
            assert secret not in everything, f"the audit record leaked {secret!r}"

    async def test_a_failing_audit_sink_changes_no_outcome(self) -> None:
        class Broken(InMemoryAuditSink):
            async def record(self, event) -> None:
                raise RuntimeError("audit store down")

        rig = _Rig(audit=Broken())
        with data_scope(INTERNAL):
            assert (await rig.call())["answers"]["total"]["value"] == 1080
        with data_scope(SENSITIVE, "memory"), pytest.raises(ToolExecutionError) as caught:
            await rig.call()
        assert "memory" in caught.value.message
