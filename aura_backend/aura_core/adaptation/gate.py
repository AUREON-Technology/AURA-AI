"""The generated evaluation gate (AP8, ADR-024): a blueprint version deploys only if it passes.

:class:`BlueprintGate` runs one stored version against a **replica** of the connected system --
the registered connectors themselves (read-only, row-capped) in production, the in-memory
replica (``adapters/connectors/memory.py``) in the default test suite -- and returns a
:class:`~aura_core.domain.gate.GateReport`. Nothing here is a parallel enforcement path: the
candidate's tools are materialised into a **throwaway** tool registry (``new_registry``: the
container passes a fresh ``ToolRegistry`` under the serving ``ToolPolicy``), exactly as a deploy
materialises them, and every call goes through ``ToolRegistry.execute`` with the :class:`ToolContext` the kernel
builds for that agent's turn (``AIKernel._tool_context``). So "the gate let it through" means "a
live turn would have let it through".

Suites, and what each proves:

``tool_contract`` (the compiler's cases)
    Every generated tool, called with identifiers the replica supplies, returns a result that
    passes its output schema **and agrees with an oracle**: a read the gate composes from the
    *twin* -- the relation's foreign-key attribute, the entity's primary key, the metric's
    measure -- not from the tool's template. A template that filters on the wrong column (a
    wrong join), returns another record, or counts the wrong rows disagrees with the oracle.
``permission_denied`` (the compiler's cases)
    An agent asked to use a tool outside its grant is refused by policy (``outside_agent_grant``),
    and a tool inside its grant is not -- so a deny-everything policy cannot pass vacuously.
``grant_scope`` (gate-owned)
    A role agent may hold exactly the capabilities ``security.permission_map`` grants its role:
    every other generated tool must be refused for it. This is the check that does not trust the
    agent's own grant, so an over-broad grant cannot hide behind a suite generated from it.
``injection_refusal`` (the compiler's cases)
    Reading the system's rows taints the turn, and an escalation attempted afterwards -- a tool
    outside the grant, as an injected instruction would ask for -- is still refused.
``model_surface`` (gate-owned)
    No text from the connected system (twin labels and descriptions, column comments, API
    summaries, the blueprint's own untrusted text) appears in any tool declaration the model
    would be sent (ADR-022).
``out_of_scope`` (the compiler's cases; advisory)
    Whether a model makes no tool call for a question the system cannot answer is a property of
    the model, not of the blueprint. Without a configured simulation model these cases are
    recorded ``not_run`` and the suite is advisory; it never blocks.

A case that cannot run -- its connector has no reachable replica, the replica holds no value to
call it with -- is ``not_run``, never a pass; each suite's coverage floor bounds how much may be
skipped.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Awaitable, Callable, Iterable, Mapping

from ..domain.ausm import ElementKind, ProjectTwin
from ..domain.blueprint import Blueprint, BlueprintTool
from ..domain.compilation import BlueprintRecord, EvalCaseSpec
from ..domain.errors import AuraError, ValidationError
from ..domain.gate import CaseOutcome, GateCaseResult, GateMode, GateReport, suite_result
from ..domain.identifiers import ToolCallId, UserId
from ..domain.messages import ToolCall
from ..domain.templates import (
    AggregateFunction,
    FilterOp,
    ReadTemplate,
    SqlAggregate,
    SqlColumn,
    SqlFilter,
    SqlSelect,
    TemplateKind,
)
from ..domain.tools import ToolSensitivity
from ..observability.logging import get_logger
from ..ports.runtime import ToolContext
from .factories.common import TwinView
from .runtime import PROJECT_DATA_CAPABILITY, BlueprintToolMaterializer, generated_agent_spec

__all__ = ["BlueprintGate", "GATE_SUITES", "DEFAULT_MIN_COVERAGE"]

logger = get_logger("adaptation.gate")

#: Suite -> (gating, threshold used when the blueprint declares none). Gate-owned suites are
#: always gating at 1.0; ``out_of_scope`` needs a model and is advisory.
GATE_SUITES: Mapping[str, tuple[bool, float]] = {
    "tool_contract": (True, 1.0),
    "permission_denied": (True, 1.0),
    "grant_scope": (True, 1.0),
    "injection_refusal": (True, 1.0),
    "model_surface": (True, 1.0),
    "out_of_scope": (False, 0.9),
}
DEFAULT_MIN_COVERAGE = 0.8
#: Rows requested from a listing tool: enough to compare, small enough never to be truncated.
_PROBE_ROWS = 10
_ORACLE_ROWS = 200
_GATE_USER = UserId("adaptation-gate")
#: Untrusted text shorter than this many words is too generic to attribute ("Employees").
_SURFACE_MIN_WORDS = 3

OpenConnector = Callable[[str], Awaitable[Any]]

#: What a harvested identifier may look like: short, no spaces, no template or path characters.
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")


class _NotRun(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class _Failed(Exception):
    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail[:300]


@dataclass(frozen=True, slots=True)
class _Probe:
    """Arguments for one call, and what the oracle says the result must be (if it can say)."""

    arguments: Mapping[str, Any]
    expected_count: int | None = None
    rows: tuple[Mapping[str, Any], ...] | None = None  # every row the result may be drawn from
    values: tuple[Any, ...] = ()                       # values a single-object result must contain


class BlueprintGate:
    def __init__(
        self,
        *,
        open_connector: OpenConnector,
        new_registry: Callable[[], Any],
        clock: Any,
        mode: GateMode = GateMode.ENFORCE,
        min_coverage: float = DEFAULT_MIN_COVERAGE,
        probe_seeds: Mapping[str, str] | None = None,
        metrics: Any = None,
    ) -> None:
        if not 0.0 <= min_coverage <= 1.0:
            raise ValidationError("min_coverage is a rate in [0, 1]")
        self._open = open_connector
        self._new_registry = new_registry
        self._clock = clock
        self._mode = mode
        self._min_coverage = min_coverage
        self._seeds = dict(probe_seeds or {})
        self._metrics = metrics

    @property
    def mode(self) -> GateMode:
        return self._mode

    async def evaluate(self, record: BlueprintRecord, twin: ProjectTwin, *, by: str) -> GateReport:
        bp = record.blueprint
        if (bp.project.twin_version, bp.project.twin_digest) != (twin.version, twin.digest):
            raise ValidationError("the gate needs the twin this blueprint was compiled from",
                                  details={"twin_version": bp.project.twin_version})
        started = time.perf_counter()
        run = _Run(self, record, twin)
        try:
            await run.setup()
            cases = await run.all_cases()
        finally:
            run.close()
        thresholds = dict(bp.evaluation.gate)
        suites = []
        for name, (gating, default) in GATE_SUITES.items():
            members = [c for c in cases if c.suite == name]
            if not members and name not in thresholds and name not in ("grant_scope", "model_surface"):
                continue
            suites.append(suite_result(name, members, gating=gating, threshold=thresholds.get(name, default),
                                       min_coverage=self._min_coverage if gating else 0.0))
        passed = all(s.ok for s in suites if s.gating)
        report = GateReport(
            project_id=record.project_id, version=record.version, digest=record.digest, mode=self._mode,
            passed=passed, ran_at=self._clock.now().astimezone(timezone.utc).isoformat(), ran_by=by,
            duration_ms=(time.perf_counter() - started) * 1000.0, suites=tuple(suites), cases=tuple(cases),
        )
        if self._metrics is not None:
            self._metrics.observe_gate(report)
        return report


class _Run:
    """One gate run: the throwaway registry, the candidate's agents, and the replica handles."""

    def __init__(self, gate: BlueprintGate, record: BlueprintRecord, twin: ProjectTwin) -> None:
        self.gate = gate
        self.record = record
        self.bp: Blueprint = record.blueprint
        self.view = TwinView(twin, {c.id: c.kind for c in self.bp.integrations})
        self.tools = gate._new_registry()
        self.materializer = BlueprintToolMaterializer(tools=self.tools, open_connector=gate._open, grace_s=0)
        self.names = dict(self.bp.tool_registry_names())
        self.by_name = {t.name: t for t in self.bp.tools}
        self.templates = {t.id: t for t in self.bp.templates}
        self.available: dict[str, bool] = {}
        self.harvest: dict[str, str] = {}
        self.agents: dict[str, Any] = {}
        self._serial = 0

    async def setup(self) -> None:
        self.materializer.register(self.bp)
        for agent in self.bp.agents:
            self.agents[agent.id] = generated_agent_spec(agent, self.names)
        for connector in self.bp.integrations:
            self.available[connector.id] = await self._reachable(connector.id)

    def close(self) -> None:
        self.materializer.unregister(self.names.values())

    async def _reachable(self, connector_id: str) -> bool:
        try:
            connector = await self.gate._open(connector_id)
        except AuraError:
            return False
        try:
            health = await connector.health()
            return bool(health.ok)
        except Exception:  # pragma: no cover - health never raises by contract
            return False
        finally:
            await connector.aclose()

    # -- cases ------------------------------------------------------------------------------

    async def all_cases(self) -> list[GateCaseResult]:
        out: list[GateCaseResult] = []
        stored = [c for suite in self.record.suites for c in suite.cases]
        for case in stored:
            out.append(await self._guarded(case.id, case.kind, self._stored(case)))
        # A tool that needs an identifier can only be called after the call that returns one, and
        # the stored order says nothing about that. Retry those once, now that results are in.
        for index, case in enumerate(stored):
            first = out[index]
            if (first.outcome is CaseOutcome.NOT_RUN and first.reason == "needs_identifier"
                    and case.kind == "tool_contract" and self.harvest):
                out[index] = await self._guarded(case.id, case.kind, self._stored(case))
        for agent in self.bp.agents:
            for case_id, coro in self._grant_scope(agent):
                out.append(await self._guarded(case_id, "grant_scope", coro))
        for case_id, check in self._model_surface():
            out.append(await self._guarded(case_id, "model_surface", check))
        return out

    async def _guarded(self, case_id: str, suite: str, check: Awaitable[str]) -> GateCaseResult:
        try:
            reason = await check
        except _NotRun as skip:
            return GateCaseResult(case_id, suite, CaseOutcome.NOT_RUN, skip.reason)
        except _Failed as failure:
            return GateCaseResult(case_id, suite, CaseOutcome.FAILED, failure.reason, failure.detail)
        except AuraError as exc:
            return GateCaseResult(case_id, suite, CaseOutcome.FAILED, f"error:{exc.kind}")
        except Exception as exc:
            # A blueprint malformed in a way no check anticipated must still produce a report
            # (and fail), not take the evaluation request down with it.
            logger.exception("gate case raised", extra={"case": case_id})
            return GateCaseResult(case_id, suite, CaseOutcome.FAILED, f"gate_error:{type(exc).__name__.lower()}")
        return GateCaseResult(case_id, suite, CaseOutcome.PASSED, reason)

    async def _stored(self, case: EvalCaseSpec) -> str:
        if case.kind == "tool_contract":
            return await self._contract(case)
        if case.kind == "permission_denied":
            return await self._permission(case)
        if case.kind == "injection_refusal":
            return await self._injection(case)
        raise _NotRun("needs_model")  # out_of_scope: a property of the model, not the blueprint

    async def _contract(self, case: EvalCaseSpec) -> str:
        tool = self.by_name.get(case.tool or "")
        if tool is None:
            raise _Failed("unknown_tool")
        probe = await self._probe(tool, case.arguments)
        data = await self._call_ok(self.agents[case.agent], tool, probe.arguments)
        self._compare(tool, data, probe)
        self._harvest_identifiers(data)
        checked = probe.expected_count is not None or probe.rows is not None or bool(probe.values)
        return "ok" if checked else "ok_schema_only"

    async def _permission(self, case: EvalCaseSpec) -> str:
        spec = self.agents[case.agent]
        context = self._context(spec)
        for name in case.forbidden_tools:
            await self._expect_denied(spec, self.by_name[name], context)
        registered = {self.names[t.name]: t for t in self.bp.tools}
        granted = next(iter(sorted(spec.allowed_tools)))
        tool = registered.get(granted)
        if tool is None:
            raise _Failed("granted_tool_refused", "the agent's grant names a tool that is not registered")
        result, _ = await self._execute(tool, self._valid_arguments(tool), context)
        if result.metadata.get("allowed") is False:
            raise _Failed("granted_tool_refused", "a tool inside the agent's grant was refused")
        return "ok"

    def _grant_scope(self, agent) -> Iterable[tuple[str, Awaitable[str]]]:
        if agent.role is None:
            return ()
        permitted = {g.capability for g in self.bp.security.permission_map if g.role == agent.role}
        spec = self.agents[agent.id]

        async def refused(tool: BlueprintTool) -> str:
            await self._expect_denied(spec, tool, self._context(spec), reason="granted_beyond_role")
            return "ok"

        return [(f"grant.{agent.id}.{tool.name}"[:160], refused(tool))
                for tool in self.bp.tools if tool.capability not in permitted]

    async def _injection(self, case: EvalCaseSpec) -> str:
        spec = self.agents[case.agent]
        context = self._context(spec)
        for name in case.expected_tools:
            tool = self.by_name[name]
            probe = await self._probe(tool, case.arguments)
            await self._call_ok(spec, tool, probe.arguments, context=context)
        if not context.tainted:
            raise _Failed("not_tainted", "reading the connected system's rows did not taint the turn")
        for name in case.forbidden_tools:
            await self._expect_denied(spec, self.by_name[name], context)
        return "ok"

    def _model_surface(self) -> Iterable[tuple[str, Awaitable[str]]]:
        corpus = sorted(_untrusted_corpus(self.bp, self.view.twin))
        declarations = self.tools.declarations(capabilities=(PROJECT_DATA_CAPABILITY,),
                                               max_sensitivity=ToolSensitivity.SYSTEM)
        generated = set(self.names.values())

        async def clean(surface: str) -> str:
            text = _normalise(surface)
            leaked = [phrase for phrase in corpus if phrase in text]
            if leaked:
                raise _Failed("connected_system_text_in_declaration",
                              f"{len(leaked)} phrase(s) from the connected system reach this declaration")
            return "ok"

        cases = []
        for declaration in declarations:
            name = str(declaration.get("name", ""))
            if name in generated:
                cases.append((f"surface.{name}"[:160], clean(json.dumps(declaration, ensure_ascii=False))))
        return cases

    # -- calls ------------------------------------------------------------------------------

    def _context(self, spec: Any) -> ToolContext:
        # What AIKernel._tool_context builds for a turn resolved to this agent (no delegation).
        self._serial += 1
        return ToolContext(
            user_id=_GATE_USER, correlation_id=f"gate-{self.record.version}-{self._serial}", deadline_s=60.0,
            agent_id=spec.id, allowed_tools=spec.allowed_tools, max_sensitivity=spec.risk_ceiling,
            memory_scope=spec.memory_scope, capabilities=(PROJECT_DATA_CAPABILITY,),
        )

    async def _execute(self, tool: BlueprintTool, arguments: Mapping[str, Any], context: ToolContext):
        self._serial += 1
        call = ToolCall(ToolCallId(f"gate-{self._serial}"), self.names[tool.name], dict(arguments))
        return await self.tools.execute(call, context, capabilities=(PROJECT_DATA_CAPABILITY,))

    async def _call_ok(self, spec: Any, tool: BlueprintTool, arguments: Mapping[str, Any], *,
                       context: ToolContext | None = None) -> Any:
        result, _ = await self._execute(tool, arguments, context or self._context(spec))
        if not result.ok:
            raise _Failed(f"tool_error:{result.error_kind or 'unknown'}", f"{tool.name} did not return a result")
        try:
            return json.loads(result.content)
        except ValueError:
            raise _Failed("unparseable_result", f"{tool.name} returned text that is not JSON") from None

    async def _expect_denied(self, spec: Any, tool: BlueprintTool, context: ToolContext, *,
                             reason: str = "not_refused") -> None:
        result, _ = await self._execute(tool, self._valid_arguments(tool), context)
        if result.metadata.get("policy_reason") != "outside_agent_grant":
            raise _Failed(reason, f"{tool.name} was not refused for {spec.id}")

    @staticmethod
    def _valid_arguments(tool: BlueprintTool) -> dict[str, Any]:
        # Well-typed values, so argument validation passes and the policy decision is what is
        # observed -- the call is expected to be refused before it reaches any connector.
        sample = {"integer": 1, "number": 1, "boolean": True}
        return {p.name: sample.get(p.type, "a") for p in tool.parameters if p.required}

    # -- probes and oracles -----------------------------------------------------------------

    def _harvest_identifiers(self, data: Any, depth: int = 0) -> None:
        """Remember the first plain-text ``*_id`` value per key from a result, to call other tools.

        The values come from the connected system, so only identifier-shaped text is kept: it
        becomes an argument to a read-only call of a tool the blueprint already declares.
        """
        if depth > 4:
            return
        if isinstance(data, dict):
            for key, value in data.items():
                if (isinstance(key, str) and key.endswith("_id") and key not in self.harvest
                        and isinstance(value, str) and _IDENTIFIER.fullmatch(value)):
                    self.harvest[key] = value
                else:
                    self._harvest_identifiers(value, depth + 1)
        elif isinstance(data, list):
            for item in data[:50]:
                self._harvest_identifiers(item, depth + 1)

    async def _probe(self, tool: BlueprintTool, fallback: Mapping[str, Any]) -> _Probe:
        if not self.available.get(tool.connector, False):
            raise _NotRun("replica_unavailable")
        template = self.templates[tool.template]
        if template.kind is not TemplateKind.SQL:
            arguments: dict[str, Any] = {}
            for param in (p for p in tool.parameters if p.required):
                # Only strings: a seed or a harvested identifier is text, and a guessed number
                # would be a made-up value the oracle cannot vouch for.
                value = self.gate._seeds.get(param.name) or self.harvest.get(param.name)
                if param.type != "string" or value is None:
                    raise _NotRun("needs_identifier")
                arguments[param.name] = value
            # A full page of results can outgrow the tool's result cap, and the registry then cuts it
            # mid-JSON; a small page keeps the probe parseable.
            for param in tool.parameters:
                if param.name in ("page_size", "limit") and param.type == "integer" and not param.required:
                    arguments[param.name] = 3
            return _Probe(arguments=arguments)
        capability, _, target_id = tool.capability.partition("@")
        target = self.view.get(target_id)
        if target is None:
            raise _Failed("target_missing")
        limited = {"limit": _PROBE_ROWS} if any(p.name == "limit" for p in tool.parameters) else {}
        required = [p.name for p in tool.parameters if p.required]
        if capability in ("entity.lookup", "state.timeline"):
            return await self._probe_by_key(tool, target, required)
        if capability == "relation.traverse":
            return await self._probe_traverse(tool, target, required, limited)
        if capability == "entity.list_filtered":
            total, rows = await self._oracle_rows(tool.connector, target)
            return _Probe(arguments=limited, expected_count=min(total, _PROBE_ROWS if limited else 50), rows=rows)
        if capability == "entity.search":
            return await self._probe_search(tool, target, limited)
        if capability == "metric.aggregate":
            return await self._probe_metric(tool, target)
        if capability == "workflow.status":
            return await self._probe_status(tool, target)
        return _Probe(arguments=dict(fallback))

    async def _probe_by_key(self, tool, entity, required) -> _Probe:
        key = self.view.primary_key(entity.id)
        if len(key) != 1 or len(required) != 1:
            raise _NotRun("needs_identifier")
        column = self.view.identifier(key[0])
        sample = await self._oracle(tool.connector, entity, columns=(SqlColumn(column, "k"),),
                                    order_by=(column,), max_rows=1)
        if not sample:
            raise _NotRun("replica_empty")
        value = sample[0]["k"]
        _, rows = await self._oracle_rows(tool.connector, entity,
                                          filters=(SqlFilter(column, FilterOp.EQ, "v"),), arguments={"v": value})
        return _Probe(arguments={required[0]: value}, expected_count=len(rows), rows=rows)

    async def _probe_traverse(self, tool, relation, required, limited) -> _Probe:
        child = self.view.get(relation.refs.get("from", ""))
        via = self.view.get(relation.refs.get("via", "")) if relation.refs.get("via") else None
        params = [name for name in required if name != "limit"]
        if child is None or via is None or len(params) != 1:
            raise _NotRun("needs_identifier")
        column = self.view.identifier(via)
        groups = await self._oracle(tool.connector, child, columns=(SqlColumn(column, "v"),),
                                    aggregates=(SqlAggregate(AggregateFunction.COUNT, "n"),),
                                    group_by=(column,), order_by=(column,), max_rows=_ORACLE_ROWS)
        value = next((g["v"] for g in groups if g["v"] is not None), None)
        if value is None:
            raise _NotRun("replica_empty")
        total, rows = await self._oracle_rows(tool.connector, child, filters=(SqlFilter(column, FilterOp.EQ, "v"),),
                                              arguments={"v": value})
        cap = _PROBE_ROWS if limited else 50
        return _Probe(arguments={params[0]: value, **limited}, expected_count=min(total, cap), rows=rows)

    async def _probe_search(self, tool, entity, limited) -> _Probe:
        template = self.templates[tool.template]
        if template.sql is None or not template.sql.any_of:
            raise _NotRun("needs_identifier")
        column = template.sql.any_of[0].column
        key = [self.view.identifier(a) for a in self.view.primary_key(entity.id)]
        sample = await self._oracle(tool.connector, entity, columns=(SqlColumn(column, "t"),),
                                    order_by=tuple(key) or (column,), max_rows=_ORACLE_ROWS)
        text = next((s["t"][:200] for s in sample if isinstance(s["t"], str) and s["t"].strip()), None)
        if text is None:
            raise _NotRun("replica_empty")
        _, rows = await self._oracle_rows(tool.connector, entity)
        # The query is a whole value of a searched column, so at least that record must match.
        return _Probe(arguments={"query": text, **limited}, expected_count=None, rows=rows, values=())

    async def _probe_metric(self, tool, metric) -> _Probe:
        entity = self.view.get(metric.refs.get("entity", ""))
        measure = metric.properties.get("measure")
        if entity is None or not isinstance(measure, str):
            raise _NotRun("needs_identifier")
        totals = await self._oracle(tool.connector, entity, columns=(), max_rows=1, aggregates=(
            SqlAggregate(AggregateFunction.COUNT, "n"), SqlAggregate(AggregateFunction.SUM, "s", measure)))
        return _Probe(arguments={}, values=(totals[0]["n"], totals[0]["s"]))

    async def _probe_status(self, tool, attribute) -> _Probe:
        entity = self.view.get(attribute.parent or "")
        if entity is None:
            raise _NotRun("needs_identifier")
        column = self.view.identifier(attribute)
        groups = await self._oracle(tool.connector, entity, columns=(SqlColumn(column, "v"),),
                                    aggregates=(SqlAggregate(AggregateFunction.COUNT, "n"),),
                                    group_by=(column,), order_by=(column,), max_rows=_ORACLE_ROWS)
        return _Probe(arguments={}, expected_count=len(groups), rows=tuple(groups))

    async def _oracle_rows(self, connector_id: str, entity, *, filters: tuple[SqlFilter, ...] = (),
                           arguments: Mapping[str, Any] | None = None) -> tuple[int, tuple[Mapping[str, Any], ...]]:
        attributes = self.view.attributes(entity.id)
        if not attributes:
            raise _NotRun("needs_identifier")
        columns = tuple(SqlColumn(self.view.identifier(a), f"c{i}") for i, a in enumerate(attributes))
        key = tuple(self.view.identifier(a) for a in self.view.primary_key(entity.id))
        counted = await self._oracle(connector_id, entity, columns=(), filters=filters, arguments=arguments,
                                     aggregates=(SqlAggregate(AggregateFunction.COUNT, "n"),), max_rows=1)
        rows = await self._oracle(connector_id, entity, columns=columns, filters=filters, arguments=arguments,
                                  order_by=key, max_rows=_ORACLE_ROWS)
        return int(counted[0]["n"]), tuple(rows)

    async def _oracle(self, connector_id: str, entity, *, columns: tuple[SqlColumn, ...],
                      filters: tuple[SqlFilter, ...] = (), aggregates: tuple[SqlAggregate, ...] = (),
                      group_by: tuple[str, ...] = (), order_by: tuple[str, ...] = (), max_rows: int,
                      arguments: Mapping[str, Any] | None = None) -> list[Mapping[str, Any]]:
        """A read composed from twin facts only, through the same connector port."""
        schema = entity.properties.get("schema")
        if not isinstance(schema, str) or entity.properties.get("source_type") != "table":
            raise _NotRun("needs_identifier")
        template = ReadTemplate("gate_oracle", connector_id, TemplateKind.SQL, sql=SqlSelect(
            schema, self.view.identifier(entity), columns=columns, filters=filters, aggregates=aggregates,
            group_by=group_by, order_by=order_by, max_rows=max_rows, limit_param=None,
        ))
        try:
            connector = await self.gate._open(connector_id)
        except AuraError:
            raise _NotRun("replica_unavailable") from None
        try:
            return list(await connector.read(template, dict(arguments or {})))
        except AuraError:
            raise _Failed("oracle_unavailable", "the replica could not answer the gate's own read") from None
        finally:
            await connector.aclose()

    @staticmethod
    def _compare(tool: BlueprintTool, data: Any, probe: _Probe) -> None:
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) and data else [])
        if probe.values:
            present = Counter(_keys(rows[0].values())) if rows else Counter()
            missing = [v for v in _keys(probe.values) if v != "null" and present[v] == 0]
            if missing:
                raise _Failed("value_mismatch", f"{tool.name} does not report the totals the replica holds")
        if probe.expected_count is not None and len(rows) != probe.expected_count:
            raise _Failed("count_mismatch", f"{tool.name} returned {len(rows)} row(s), the oracle {probe.expected_count}")
        if probe.rows is not None:
            pool = [Counter(_keys(r.values())) for r in probe.rows]
            for row in rows:
                wanted = Counter(_keys(row.values()))
                index = next((i for i, have in enumerate(pool) if not wanted - have), None)
                if index is None:
                    raise _Failed("row_mismatch", f"{tool.name} returned a row the oracle does not hold")
                pool.pop(index)
            if probe.expected_count is None and not rows:
                raise _Failed("count_mismatch", f"{tool.name} found nothing for a value the replica holds")


def _keys(values: Iterable[Any]) -> list[str]:
    return [json.dumps(v, sort_keys=True, ensure_ascii=False, default=str) for v in values]


# ------------------------------------------------------------------------------- model surface


def _normalise(text: str) -> str:
    return " ".join(text.lower().split())


def _untrusted_corpus(bp: Blueprint, twin: ProjectTwin) -> set[str]:
    """Every phrase the connected system wrote (or a labeller derived from it) long enough to be
    attributable: twin labels and descriptions, and the blueprint's own untrusted text."""
    phrases: set[str] = set()

    def add(text: Any) -> None:
        raw = text.raw() if hasattr(text, "raw") else text
        if isinstance(raw, str):
            norm = _normalise(raw)
            if len(norm.split()) >= _SURFACE_MIN_WORDS:
                phrases.add(norm)

    for element in twin.elements:
        add(element.label)
        if element.description is not None:
            add(element.description)
        if element.kind is ElementKind.STATE:
            add(element.properties.get("value"))
    for tool in bp.tools:
        add(tool.description)
    for agent in bp.agents:
        add(agent.name)
    for view in bp.ui.views:
        add(view.title)
    return phrases

