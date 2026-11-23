"""Regression tests from the tool-engine review (registry, policy, schema, domain/tools, delegation).

Each test below failed on the code as it stood before the review, and drives the real
``ToolRegistry`` / ``ToolPolicy`` / ``validate_arguments`` -- no mocks of the engine itself. Where a
collaborator is needed (an audit sink, a metrics sink, a result guard, a kernel) it is a small real
object that records what it is given.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import time
from types import SimpleNamespace
from typing import Any, Mapping

import pytest

from aura_core.agents.registry import build_default_agent_registry
from aura_core.domain import ToolCall, ToolCallId, UserId
from aura_core.domain.errors import ValidationError
from aura_core.domain.tools import (
    NESTED_DISPATCH_TAG,
    ParameterSpec,
    ToolCategory,
    ToolSensitivity,
    ToolSpec,
)
from aura_core.ports.runtime import ToolContext
from aura_core.safety.pipeline import SafetyPipeline
from aura_core.tools import ToolPolicy, ToolRegistry, param, tool
from aura_core.tools import registry as registry_module
from aura_core.tools.base import FunctionTool
from aura_core.tools.delegation import build_delegation_tools
from aura_core.tools.schema import _TEMPLATE_TOKEN, _has_template_token, validate_arguments


def _ctx(**kw: Any) -> ToolContext:
    return ToolContext(user_id=UserId("u1"), correlation_id="c1", deadline_s=30.0, **kw)


def _call(name: str, arguments: Any = None, call_id: str = "call-1") -> ToolCall:
    return ToolCall(ToolCallId(call_id), name, {} if arguments is None else arguments)


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, str]] = []

    async def record(self, event: Any) -> None:
        self.events.append((event.action, event.outcome, event.detail["reason"], event.resource))


class _Counter:
    def __init__(self, sink: list[dict[str, str]], labels: dict[str, str]) -> None:
        self._sink, self._labels = sink, labels

    def inc(self) -> None:
        self._sink.append(self._labels)

    def observe(self, _value: float) -> None:  # tool_duration
        pass


class _Family:
    def __init__(self, sink: list[dict[str, str]]) -> None:
        self._sink = sink

    def labels(self, **labels: str) -> _Counter:
        return _Counter(self._sink, labels)


class _Metrics:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []
        self.denied: list[dict[str, str]] = []
        self.tool_calls_total = _Family(self.calls)
        self.tool_denied_total = _Family(self.denied)
        self.tool_duration = _Family([])


def _function_tool(name: str, handler: Any, **spec_kwargs: Any) -> FunctionTool:
    return FunctionTool(ToolSpec(name=name, description="d", **spec_kwargs), handler)


# ------------------------------------------------------------------------------ schema.py


class TestNumbersMustBeFinite:
    """NaN compares False against every bound, so it used to pass ``minimum``/``maximum``."""

    @staticmethod
    def _spec() -> ToolSpec:
        @tool("amount", "x", param("n", "number", "n", minimum=0, maximum=10),
              param("i", "integer", "i", required=False, maximum=5),
              param("floor_only", "number", "f", required=False, minimum=0))
        async def amount(n: float, i: int | None = None, floor_only: float | None = None) -> str:
            return "ok"

        return amount.spec

    @pytest.mark.parametrize("bad", [float("nan"), "nan", "NaN", float("inf"), "inf", "-inf", 1e400, "1e400"])
    def test_a_non_finite_number_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(self._spec(), {"n": bad})

    def test_infinity_is_refused_even_when_only_the_other_bound_is_declared(self) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(self._spec(), {"n": 1, "floor_only": float("inf")})

    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), "nan"])
    def test_a_non_finite_integer_is_refused(self, bad: Any) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(self._spec(), {"n": 1, "i": bad})

    def test_ordinary_numbers_still_pass(self) -> None:
        assert validate_arguments(self._spec(), {"n": "2.5", "i": "3"}) == {"n": 2.5, "i": 3}

    def test_a_bool_is_still_not_a_number(self) -> None:
        with pytest.raises(ValidationError):
            validate_arguments(self._spec(), {"n": True})


class TestUnknownKeys:
    def test_non_string_keys_of_mixed_types_are_a_refusal_not_a_typeerror(self) -> None:
        spec = TestNumbersMustBeFinite._spec()
        with pytest.raises(ValidationError):
            validate_arguments(spec, {1: "a", "b": 2, "n": 1})


class TestTemplateTokenScanIsLinear:
    def test_repeated_openers_with_no_closing_brace_do_not_stall_the_event_loop(self) -> None:
        spec = ParameterSpec(name="text", type="string", description="d")
        tool_spec = ToolSpec(name="t", description="d", parameters=(spec,))
        payload = "${" * 150_000  # 300k chars: seven-plus seconds with the old pattern, then more
        started = time.perf_counter()
        assert validate_arguments(tool_spec, {"text": payload}) == {"text": payload}
        assert time.perf_counter() - started < 1.0

    def test_a_real_token_is_still_refused(self) -> None:
        spec = ParameterSpec(name="text", type="string", description="d")
        tool_spec = ToolSpec(name="t", description="d", parameters=(spec,))
        for text in ("${step.output}", "x ${ y } z", "${}", "}${a}", "${ ${a}"):
            with pytest.raises(ValidationError):
                validate_arguments(tool_spec, {"text": text})

    def test_the_linear_scan_agrees_with_the_pattern_it_replaces(self) -> None:
        rng = random.Random(20261122)
        for _ in range(4000):
            text = "".join(rng.choice("${}ab ") for _ in range(rng.randint(0, 14)))
            assert _has_template_token(text) == bool(_TEMPLATE_TOKEN.search(text)), text


# ------------------------------------------------------------------------- domain/tools.py


class TestToolSpecInvariants:
    @pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
    def test_timeout_must_be_positive_and_finite(self, bad: float) -> None:
        with pytest.raises(ValueError):
            ToolSpec(name="t", description="d", timeout_s=bad)

    @pytest.mark.parametrize("bad", [0, -5])
    def test_a_result_cap_below_one_is_refused(self, bad: int) -> None:
        # content[:-5] would keep everything but the last five characters.
        with pytest.raises(ValueError):
            ToolSpec(name="t", description="d", max_result_chars=bad)


# ----------------------------------------------------------------------------- policy.py


class TestExplicitConfirmationSurvivesWorkspaceLocal:
    @staticmethod
    def _spec(**kw: Any) -> ToolSpec:
        return ToolSpec(name="scratch_write", description="d", side_effecting=True,
                        workspace_local=True, sensitivity=ToolSensitivity.PERSONAL, **kw)

    def test_an_ordinary_workspace_local_tool_is_still_exempt(self) -> None:
        decision = ToolPolicy().evaluate(self._spec(), context=_ctx(tainted=True))
        assert decision.allowed and not decision.requires_confirmation
        assert decision.reason == "workspace_local_side_effect"

    def test_a_workspace_local_tool_that_declares_confirmation_is_held(self) -> None:
        decision = ToolPolicy().evaluate(self._spec(requires_confirmation=True), context=_ctx())
        assert decision.allowed and decision.requires_confirmation
        assert decision.reason == "tool_requires_confirmation"

    async def test_through_the_registry_it_asks_and_does_not_run(self) -> None:
        ran: list[str] = []

        async def handler() -> str:
            ran.append("x")
            return "done"

        registry = ToolRegistry()
        registry.register(FunctionTool(self._spec(requires_confirmation=True), handler))
        result, report = await registry.execute(_call("scratch_write"), _ctx())
        assert report.needs_confirmation and result.error_kind == "confirmation_required"
        assert ran == []


class TestTaintAndConfirmation:
    """Pins the rules the review attacked and found sound, so they stay that way."""

    @staticmethod
    def _registry(effects: list[str]) -> ToolRegistry:
        @tool("fetch_page", "Fetch.", category=ToolCategory.KNOWLEDGE, tags=("untrusted_content",))
        async def fetch_page() -> str:
            await asyncio.sleep(0.01)
            return "page"

        @tool("save_note", "Save.", param("text", "string", "t"), category=ToolCategory.PRODUCTIVITY,
              sensitivity=ToolSensitivity.PERSONAL, side_effecting=True)
        async def save_note(text: str) -> str:
            effects.append(text)
            return "saved"

        registry = ToolRegistry()
        registry.register_all([fetch_page, save_note])
        return registry

    async def test_a_personal_side_effect_on_a_tainted_turn_needs_confirmation(self) -> None:
        effects: list[str] = []
        registry = self._registry(effects)
        result, report = await registry.execute(_call("save_note", {"text": "a"}), _ctx(tainted=True))
        assert report.needs_confirmation and effects == []
        confirmed, _ = await registry.execute(_call("save_note", {"text": "a"}), _ctx(tainted=True),
                                              confirmed=True)
        assert confirmed.ok and effects == ["a"]

    async def test_execute_many_defers_the_gated_call_until_the_taint_has_landed(self) -> None:
        effects: list[str] = []
        registry = self._registry(effects)
        outcomes = await registry.execute_many(
            [_call("fetch_page", call_id="f"), _call("save_note", {"text": "b"}, call_id="s")], _ctx()
        )
        assert outcomes[1][1].needs_confirmation and effects == []

    async def test_a_denied_capability_is_denied_even_with_confirmation(self) -> None:
        @tool("gated", "g", required_capability="engineer_agent", side_effecting=True,
              sensitivity=ToolSensitivity.CONFIDENTIAL)
        async def gated() -> str:
            return "ran"

        registry = ToolRegistry()
        registry.register(gated)
        result, report = await registry.execute(_call("gated"), _ctx(), confirmed=True)
        assert result.error_kind == "policy_denied" and not report.allowed

    async def test_a_name_that_differs_by_case_or_whitespace_is_not_the_tool(self) -> None:
        registry = self._registry([])
        for name in ("Save_Note", "save_note ", " save_note", "save_note​", "ѕave_note"):
            result, report = await registry.execute(_call(name, {"text": "x"}), _ctx(), confirmed=True)
            assert not report.allowed and report.policy_reason == "tool_not_found"


# --------------------------------------------------------------------------- registry.py


class TestMalformedArgumentsNeverEscapeExecute:
    @staticmethod
    def _registry() -> ToolRegistry:
        @tool("echo_it", "e", param("text", "string", "t", required=False))
        async def echo_it(text: str = "") -> str:
            return text

        registry = ToolRegistry()
        registry.register(echo_it)
        return registry

    async def test_arguments_that_are_not_an_object_are_refused_in_the_reply(self) -> None:
        result, report = await self._registry().execute(_call("echo_it", [1, 2]), _ctx())
        assert not result.ok and result.error_kind == "validation_error"
        assert "must be an object" in result.content

    async def test_keys_of_mixed_types_are_refused_in_the_reply(self) -> None:
        result, _ = await self._registry().execute(_call("echo_it", {1: "a", "b": 2}), _ctx())
        assert not result.ok and result.error_kind == "validation_error"

    async def test_arguments_nested_past_the_recursion_limit_are_refused_in_the_reply(self) -> None:
        deepest: dict[str, Any] = {}
        node = deepest
        for _ in range(2000):
            node["a"] = {}
            node = node["a"]
        result, _ = await self._registry().execute(_call("echo_it", deepest), _ctx())
        assert not result.ok and result.error_kind == "validation_error"


class TestStructuredOutputSurvivesTheGuard:
    SCHEMA = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "n": {"type": "integer"}},
        "required": ["title"],
        "additionalProperties": False,
    }

    @staticmethod
    def _registry(payload: str, schema: Mapping[str, Any], guard: Any = None, **kw: Any) -> ToolRegistry:
        async def handler() -> str:
            return payload

        pipeline = SafetyPipeline()

        async def real_guard(text: str, name: str, context: ToolContext) -> Any:
            return await pipeline.guard_tool_result(text, tool=name, user_id=context.user_id)

        registry = ToolRegistry(result_guard=guard or real_guard, audit=kw.pop("audit", None))
        registry.register(_function_tool("emit", handler, output_schema=schema, **kw))
        return registry

    async def test_a_fullwidth_quote_cannot_be_folded_into_new_keys_after_validation(self) -> None:
        # Valid before the guard (one string value). NFKC turns the full-width quotes into real
        # ones, so the rewritten text would parse as {"title": "x", "role": "admin", ...}.
        payload = json.dumps({"title": "x＂, ＂role＂: ＂admin", "n": 1}, ensure_ascii=False)
        audit = _Audit()
        result, report = await self._registry(payload, self.SCHEMA, audit=audit).execute(
            _call("emit"), _ctx())
        assert not result.ok and result.error_kind == "invalid_output"
        assert "role" in result.content and "admin" not in result.content
        assert audit.events == [("tool.emit", "error", "invalid_output", "emit")]

    async def test_a_guard_that_cuts_the_document_cannot_hand_on_broken_json(self) -> None:
        payload = json.dumps({"title": "t" * 400, "n": 1})

        async def truncating_guard(text: str, name: str, context: ToolContext) -> Any:
            cut = text[:100] + "\n[…truncated by size guard]"
            return SimpleNamespace(allowed=True, text=cut, tainted=False, report=None)

        result, _ = await self._registry(payload, self.SCHEMA, guard=truncating_guard).execute(
            _call("emit"), _ctx())
        assert not result.ok and result.error_kind == "invalid_output"

    async def test_a_credential_inside_a_value_is_still_redacted_and_the_result_stays_valid(self) -> None:
        key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"
        payload = json.dumps({"title": f"config uses {key} here", "n": 2})
        result, _ = await self._registry(payload, self.SCHEMA).execute(_call("emit"), _ctx())
        assert result.ok and key not in result.content
        assert json.loads(result.content)["n"] == 2

    async def test_a_clean_structured_result_is_untouched(self) -> None:
        payload = json.dumps({"title": "plain", "n": 3})
        result, _ = await self._registry(payload, self.SCHEMA).execute(_call("emit"), _ctx())
        assert result.ok and json.loads(result.content) == {"title": "plain", "n": 3}

    async def test_free_text_is_still_redacted_without_a_schema(self) -> None:
        key = "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3"

        async def handler() -> str:
            return f"api key is {key} ok"

        pipeline = SafetyPipeline()

        async def guard(text: str, name: str, context: ToolContext) -> Any:
            return await pipeline.guard_tool_result(text, tool=name)

        registry = ToolRegistry(result_guard=guard)
        registry.register(_function_tool("plain", handler))
        result, _ = await registry.execute(_call("plain"), _ctx())
        assert result.ok and key not in result.content


class TestDeepOutputDoesNotEscapeAfterTheToolActed:
    async def test_a_result_nested_past_the_recursion_limit_is_a_failed_call_and_is_audited(self) -> None:
        acted: list[str] = []

        async def handler() -> str:
            acted.append("wrote")
            return "[" * 6000 + "]" * 6000

        audit = _Audit()
        registry = ToolRegistry(audit=audit)
        registry.register(_function_tool(
            "writer", handler, output_schema={"type": "array"}, side_effecting=True,
            sensitivity=ToolSensitivity.PERSONAL))
        result, _ = await registry.execute(_call("writer"), _ctx())
        assert acted == ["wrote"]
        assert not result.ok and result.error_kind == "invalid_output"
        assert audit.events == [("tool.writer", "error", "invalid_output", "writer")]

    async def test_an_oversized_deep_result_is_too_large_not_a_crash(self) -> None:
        async def handler() -> str:
            return "[" * 6000 + "]" * 6000

        registry = ToolRegistry()
        registry.register(_function_tool("deep", handler, output_schema={"type": "array"},
                                         max_result_chars=100))
        result, _ = await registry.execute(_call("deep"), _ctx())
        assert not result.ok


class TestAuditCompleteness:
    async def test_an_unexpected_exception_from_a_class_based_tool_is_audited(self) -> None:
        class Broken:
            spec = ToolSpec(name="broken", description="d")

            async def execute(self, arguments: Mapping[str, Any], context: ToolContext) -> str:
                raise RuntimeError("bug")

        audit = _Audit()
        registry = ToolRegistry(audit=audit)
        registry.register(Broken())
        result, _ = await registry.execute(_call("broken"), _ctx())
        assert result.error_kind == "tool_execution_error"
        assert audit.events == [("tool.broken", "error", "tool_execution_error", "broken")]


class TestModelChosenNamesAreBounded:
    async def test_unknown_names_share_one_metrics_label_and_are_cut_in_audit_and_reply(self) -> None:
        metrics, audit = _Metrics(), _Audit()
        registry = ToolRegistry(metrics=metrics, audit=audit)
        long_name = "x" * 50_000
        for name in (long_name, "made_up_1", "made_up_2"):
            result, _ = await registry.execute(_call(name), _ctx())
            assert len(result.content) < 600
        assert {labels["tool"] for labels in metrics.calls} == {"_unknown"}
        assert all(len(resource) < 200 and len(action) < 220 for action, _, _, resource in audit.events)
        assert len(audit.events) == 3


class TestPreviewCannotHangConfirmation:
    async def test_a_preview_that_never_returns_degrades_to_no_diff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(registry_module, "PREVIEW_TIMEOUT_S", 0.05)

        async def stuck(arguments: Mapping[str, Any], context: ToolContext) -> str | None:
            await asyncio.sleep(30)
            return "never"

        async def handler() -> str:
            return "ran"

        registry = ToolRegistry()
        registry.register(_function_tool("push", handler, side_effecting=True,
                                         sensitivity=ToolSensitivity.CONFIDENTIAL, preview=stuck))
        started = time.perf_counter()
        result, report = await registry.execute(_call("push"), _ctx())
        assert time.perf_counter() - started < 2.0
        assert report.needs_confirmation and report.diff_preview is None


class TestNestedDispatchDoesNotStarveTheRegistry:
    @staticmethod
    def _registry(*, tagged: bool) -> ToolRegistry:
        registry = ToolRegistry(max_parallel=2)

        @tool("leaf", "leaf")
        async def leaf() -> str:
            await asyncio.sleep(0.01)
            return "leaf-done"

        async def outer(context: ToolContext) -> str:
            await asyncio.sleep(0.05)  # let every outer take its place first
            inner, _ = await registry.execute(_call("leaf", call_id="inner"), context)
            return inner.content

        registry.register(leaf)
        registry.register(_function_tool(
            "outer", outer, timeout_s=1.0, tags=(NESTED_DISPATCH_TAG,) if tagged else ()))
        return registry

    async def test_as_many_concurrent_nested_calls_as_there_are_slots_all_finish(self) -> None:
        registry = self._registry(tagged=True)
        started = time.perf_counter()
        outcomes = await asyncio.gather(
            *(registry.execute(_call("outer", call_id=f"o{i}"), _ctx()) for i in range(4)))
        assert all(r.ok and r.content == "leaf-done" for r, _ in outcomes)
        assert time.perf_counter() - started < 0.9

    async def test_leaf_work_is_still_bounded_by_the_slots(self) -> None:
        registry = ToolRegistry(max_parallel=2)
        running = peak = 0

        @tool("leaf", "leaf")
        async def leaf() -> str:
            nonlocal running, peak
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.02)
            running -= 1
            return "x"

        registry.register(leaf)
        await asyncio.gather(*(registry.execute(_call("leaf", call_id=f"l{i}"), _ctx()) for i in range(8)))
        assert peak == 2

    def test_delegate_to_agent_carries_the_tag(self) -> None:
        tools = build_delegation_tools(agents=build_default_agent_registry(), kernel=lambda: None)
        spec = tools[0].spec
        assert spec.name == "delegate_to_agent"
        assert NESTED_DISPATCH_TAG in spec.tags and "untrusted_content" in spec.tags

    async def test_concurrent_real_delegations_through_a_small_registry_complete(self) -> None:
        registry = ToolRegistry(max_parallel=2)

        @tool("lookup", "lookup")
        async def lookup() -> str:
            await asyncio.sleep(0.01)
            return "found"

        registry.register(lookup)

        class Kernel:
            async def run(self, request: Any) -> Any:
                inner, _ = await registry.execute(
                    _call("lookup", call_id="child"), _ctx(delegation_depth=request.delegation_depth))
                await asyncio.sleep(0.05)
                return SimpleNamespace(text=inner.content, needs_confirmation=(), pending_tool_calls=())

        agents = build_default_agent_registry()
        (delegate,) = build_delegation_tools(agents=agents, kernel=lambda: Kernel(), deadline_s=1.0)
        registry.register(delegate)
        outcomes = await asyncio.gather(*(
            registry.execute(
                _call("delegate_to_agent", {"target_agent_id": "finance_advisor", "subtask": "s"},
                      call_id=f"d{i}"),
                _ctx(agent_id="orchestrator"),
            )
            for i in range(4)
        ))
        assert all(r.ok and r.content == "found" for r, _ in outcomes), [r.content for r, _ in outcomes]


# ----------------------------------------------------------------- persona-grant audit


def _all_tools() -> dict[str, ToolSpec]:
    from aura_core.tools.builtin import default_tools

    async def resolver(_user: Any) -> None:
        return None

    class _Ocr:  # a structured OCR engine, so ocr_image and analyze_image_requirements register (ADR-040)
        name = "ocr"

        async def image_text(self, image, *, page):
            return ""

        async def image_data(self, image, *, page):
            raise NotImplementedError

    class _Stt:  # a timestamp-capable speech engine, so transcribe_audio registers
        name = "stt"

        async def transcribe(self, audio, *, language=None):
            return ""

        async def transcribe_segments(self, audio, *, language=None, word_timestamps=True):
            raise NotImplementedError

    tools = default_tools(
        knowledge_engine=object(), memory_engine=object(), personal_os_store=object(),
        workspace="/tmp", allowed_http_hosts=("example.com",), enable_weather=True,
        sandbox_runner=object(), github_client_resolver=resolver, github_default_repo="a/b",
        workspace_manager=object(), host_git=object(), github_token_resolver=resolver,
        host_agent_registry=object(), enable_analysis_tools=True, enable_open_exec_tools=True,
        enable_desktop_ops_tools=True, episodic_memory=object(),
        enable_open_pure_tools=True,  # ADR-042
        web_search_url="http://searxng:8080", ocr_engine=_Ocr(), vision_engine=object(),
        image_normalizer=object(), stt_engine=_Stt(),
    )
    agents = build_default_agent_registry()
    tools += build_delegation_tools(agents=agents, kernel=lambda: None)
    return {t.spec.name: t.spec for t in tools}


class TestPersonaGrantsAgainstTheRealToolSet:
    def test_every_granted_name_is_a_tool_that_registers(self) -> None:
        specs = _all_tools()
        for agent in build_default_agent_registry():
            if agent.allowed_tools is None:
                continue
            assert sorted(n for n in agent.allowed_tools if n not in specs) == [], agent.id

    def test_no_persona_but_the_orchestrator_can_delegate(self) -> None:
        for agent in build_default_agent_registry():
            assert agent.permits_tool("delegate_to_agent") == (agent.id == "orchestrator")

    def test_no_persona_is_granted_a_tool_above_its_own_risk_ceiling(self) -> None:
        specs = _all_tools()
        for agent in build_default_agent_registry():
            for name in agent.allowed_tools or ():
                assert specs[name].sensitivity <= agent.risk_ceiling, (agent.id, name)

    def test_only_the_engineer_holds_engineer_capability_tools(self) -> None:
        specs = _all_tools()
        for agent in build_default_agent_registry():
            if agent.id in ("orchestrator", "engineer"):
                continue
            gated = [n for n in agent.allowed_tools or () if specs[n].required_capability]
            assert gated == [], (agent.id, gated)

    def test_the_read_only_persona_holds_no_side_effecting_tool(self) -> None:
        specs = _all_tools()
        decision = build_default_agent_registry().get("decision_agent")
        assert [n for n in decision.allowed_tools or () if specs[n].side_effecting] == []

    def test_confirmation_gated_and_network_tools_are_not_granted_to_personal_data_personas(self) -> None:
        specs = _all_tools()
        agents = build_default_agent_registry()
        for agent_id in ("task_planner", "finance_advisor", "health_companion", "coding_agent"):
            granted = agents.get(agent_id).allowed_tools or frozenset()
            assert not [n for n in granted if specs[n].requires_confirmation], agent_id
            assert not [n for n in granted if "untrusted_content" in specs[n].tags], agent_id

    def test_an_untrusted_content_persona_still_gets_confirmation_for_side_effects_when_tainted(self) -> None:
        specs = _all_tools()
        research = build_default_agent_registry().get("research_agent")
        assert "fetch_url" in (research.allowed_tools or ())
        remember = specs["remember_fact"]
        decision = ToolPolicy().evaluate(remember, context=_ctx(tainted=True))
        assert decision.requires_confirmation
        assert re.fullmatch(r"side_effect_in_tainted_context", decision.reason)


class TestRefusalsAndHoldsAreAudited:
    @staticmethod
    def _registry(audit: _Audit) -> ToolRegistry:
        @tool("save", "s", param("n", "integer", "n"), sensitivity=ToolSensitivity.PERSONAL,
              side_effecting=True)
        async def save(n: int) -> str:
            return "saved"

        registry = ToolRegistry(audit=audit)
        registry.register(save)
        return registry

    async def test_invalid_arguments_leave_an_audit_record_with_a_digest_not_the_values(self) -> None:
        audit = _Audit()
        await self._registry(audit).execute(_call("save", {"n": "secret-value"}), _ctx())
        assert audit.events == [("tool.save", "denied", "invalid_arguments", "save")]

    async def test_a_call_held_for_confirmation_leaves_an_audit_record(self) -> None:
        audit = _Audit()
        _, report = await self._registry(audit).execute(_call("save", {"n": 1}), _ctx(tainted=True))
        assert report.needs_confirmation
        assert audit.events == [("tool.save", "needs_confirmation", "side_effect_in_tainted_context", "save")]
