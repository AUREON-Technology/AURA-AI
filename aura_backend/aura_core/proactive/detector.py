"""Detection rules: analyse events and emit quality signals.

Each rule observes specific event kinds and decides whether quality has degraded
enough to warrant attention. Rules are composable and testable in isolation.
"""

from __future__ import annotations

import math
from typing import Any

from .event import ProactiveEvent
from .signal import QualitySignal, SignalSeverity


class DetectionRule:
    """A rule that turns events into quality signals.

    A rule receives a stream of events and produces zero or more signals when
    a degradation pattern is detected. Rules are registered with the
    :class:`~aura_core.proactive.loop.ProactiveLoop` which manages the event
    stream and fan-out.
    """

    def __init__(self, name: str, *, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled

    def check(self, event: ProactiveEvent) -> tuple[QualitySignal | None, ...]:
        """Check one event against this rule.

        Returns a tuple of QualitySignal (empty if no signal).
        Subclasses should override :meth:`_check` for the actual logic.
        """
        if not self.enabled:
            return ()
        return self._check(event)

    def _check(self, event: ProactiveEvent) -> tuple[QualitySignal, ...]:
        """Override in subclasses to implement detection logic."""
        return ()


class RetrievalQualityRule(DetectionRule):
    """Detect retrieval quality degradation.

    Triggers when recall_at_k or precision_at_k falls below acceptable thresholds,
    or when empty retrievals become frequent.
    """

    def __init__(
        self,
        name: str,
        *,
        min_recall: float = 0.3,
        min_precision: float = 0.3,
        max_empty_fraction: float = 0.2,
        window_size: int = 20,
        enabled: bool = True,
    ) -> None:
        super().__init__(name, enabled=enabled)
        self.min_recall = min_recall
        self.min_precision = min_precision
        self.max_empty_fraction = max_empty_fraction
        self.window_size = window_size
        # Per-strategy state: list of (recall, precision, was_empty) tuples
        self._history: dict[str, list[tuple[float, float, bool]]] = {}

    def _check(self, event: ProactiveEvent) -> tuple[QualitySignal, ...]:
        if event.kind != "retrieval":
            return ()

        payload = event.payload
        strategy = payload.get("strategy", "unknown")
        recall = float(payload.get("recall_at_k", 1.0))
        precision = float(payload.get("precision_at_k", 1.0))
        was_empty = bool(payload.get("empty", False))

        # Maintain per-strategy sliding window
        if strategy not in self._history:
            self._history[strategy] = []
        self._history[strategy].append((recall, precision, was_empty))
        if len(self._history[strategy]) > self.window_size:
            self._history[strategy].pop(0)

        # Need enough data to decide
        history = self._history[strategy]
        if len(history) < 5:
            return ()

        # Check recent recall/precision
        recent_recalls = [r for r, _, _ in history[-5:]]
        recent_precisions = [p for _, p, _ in history[-5:]]
        recent_empty_counts = sum(1 for _, _, w in history[-5:] if w)

        avg_recall = sum(recent_recalls) / len(recent_recalls)
        avg_precision = sum(recent_precisions) / len(recent_precisions)
        empty_fraction = recent_empty_counts / len(recent_recalls)

        signals: list[QualitySignal] = []

        if avg_recall < self.min_recall:
            signals.append(QualitySignal(
                id=f"{self.name}-low-recall",
                severity=SignalSeverity.WARNING,
                title=f"Low retrieval recall ({avg_recall:.2f} < {self.min_recall:.2f})",
                description=f"Recall has degraded over the last {len(recent_recalls)} retrieval events. "
                            f"Consider index refresh or query expansion.",
                metric_name="recall_at_k",
                metric_value=avg_recall,
                baseline_value=self.min_recall,
                turn_ids=[event.id],
                suggested_action="investigate",
                auto_approvable=False,
            ))

        if avg_precision < self.min_precision:
            signals.append(QualitySignal(
                id=f"{self.name}-low-precision",
                severity=SignalSeverity.WARNING,
                title=f"Low retrieval precision ({avg_precision:.2f} < {self.min_precision:.2f})",
                description=f"Precision has degraded over the last {len(recent_precisions)} retrieval events. "
                            f"Consider query refinement or reranking.",
                metric_name="precision_at_k",
                metric_value=avg_precision,
                baseline_value=self.min_precision,
                turn_ids=[event.id],
                suggested_action="investigate",
                auto_approvable=False,
            ))

        if empty_fraction > self.max_empty_fraction:
            signals.append(QualitySignal(
                id=f"{self.name}-empty-frequent",
                severity=SignalSeverity.CRITICAL,
                title=f"Frequent empty retrievals ({empty_fraction:.0%} of last {len(recent_recalls)})",
                description=f"More than {self.max_empty_fraction:.0%} of retrieval events returned no results. "
                            f"This indicates a broken index or threshold issue.",
                metric_name="retrieval_empty_fraction",
                metric_value=empty_fraction,
                baseline_value=self.max_empty_fraction,
                turn_ids=[event.id],
                suggested_action="index_refresh",
                auto_approvable=True,
            ))

        return tuple(signals)


class AnswerQualityRule(DetectionRule):
    """Detect answer quality degradation.

    Triggers when token_f1, substring_coverage, or groundedness falls below thresholds,
    or when refusal rate is unexpectedly high/low.
    """

    def __init__(
        self,
        name: str,
        *,
        min_token_f1: float = 0.5,
        min_groundedness: float = 0.6,
        max_refusal_rate: float | None = None,
        window_size: int = 15,
        enabled: bool = True,
    ) -> None:
        super().__init__(name, enabled=enabled)
        self.min_token_f1 = min_token_f1
        self.min_groundedness = min_groundedness
        self.max_refusal_rate = max_refusal_rate
        self.window_size = window_size
        self._history: list[dict[str, Any]] = []

    def _check(self, event: ProactiveEvent) -> tuple[QualitySignal, ...]:
        if event.kind != "turn_complete":
            return ()

        payload = event.payload
        token_f1 = float(payload.get("token_f1", 1.0))
        groundedness = float(payload.get("groundedness", 1.0))
        refused = bool(payload.get("refused", False))

        self._history.append({
            "token_f1": token_f1,
            "groundedness": groundedness,
            "refused": refused,
        })
        if len(self._history) > self.window_size:
            self._history.pop(0)

        if len(self._history) < 5:
            return ()

        recent = self._history[-5:]
        avg_f1 = sum(r["token_f1"] for r in recent) / len(recent)
        avg_groundedness = sum(r["groundedness"] for r in recent) / len(recent)
        refusal_count = sum(1 for r in recent if r["refused"])
        refusal_rate = refusal_count / len(recent)

        signals: list[QualitySignal] = []

        if token_f1 < self.min_token_f1:
            signals.append(QualitySignal(
                id=f"{self.name}-low-f1",
                severity=SignalSeverity.WARNING,
                title=f"Low answer token F1 ({avg_f1:.2f} < {self.min_token_f1:.2f})",
                description=f"Answer quality has declined. Token overlap with expected answers "
                            f"is below threshold. Consider RAG pipeline review or context improvement.",
                metric_name="token_f1",
                metric_value=avg_f1,
                baseline_value=self.min_token_f1,
                turn_ids=[event.id],
                suggested_action="investigate",
                auto_approvable=False,
            ))

        if groundedness < self.min_groundedness:
            signals.append(QualitySignal(
                id=f"{self.name}-low-grounded",
                severity=SignalSeverity.CRITICAL,
                title=f"Low groundedness ({avg_groundedness:.2f} < {self.min_groundedness:.2f})",
                description=f"Answers contain claims without supporting citations. "
                            f"This is a hallucination risk. Review knowledge base and attribution.",
                metric_name="groundedness",
                metric_value=avg_groundedness,
                baseline_value=self.min_groundedness,
                turn_ids=[event.id],
                suggested_action="investigate",
                auto_approvable=False,
            ))

        if self.max_refusal_rate is not None:
            if refusal_rate > self.max_refusal_rate:
                signals.append(QualitySignal(
                    id=f"{self.name}-high-refusal",
                    severity=SignalSeverity.WARNING,
                    title=f"High refusal rate ({refusal_rate:.0%} > {self.max_refusal_rate:.0%})",
                    description=f"Refusal rate has spiked. System is declining to answer more often. "
                                f"Check knowledge coverage and safety guard thresholds.",
                    metric_name="refusal_rate",
                    metric_value=refusal_rate,
                    baseline_value=self.max_refusal_rate,
                    turn_ids=[event.id],
                    suggested_action="investigate",
                    auto_approvable=False,
                ))
            elif self.max_refusal_rate > 0 and refusal_rate < self.max_refusal_rate * 0.5:
                # Unusually low refusal might mean safety gates are too permissive
                signals.append(QualitySignal(
                    id=f"{self.name}-low-refusal",
                    severity=SignalSeverity.INFO,
                    title=f"Unusually low refusal rate ({refusal_rate:.0%})",
                    description=f"Refusal rate is very low, which may indicate safety gates are too permissive. "
                                f"Review security policy configuration.",
                    metric_name="refusal_rate",
                    metric_value=refusal_rate,
                    baseline_value=self.max_refusal_rate,
                    turn_ids=[event.id],
                    suggested_action="monitor",
                    auto_approvable=False,
                ))

        return tuple(signals)


class ToolSuccessRule(DetectionRule):
    """Detect tool execution failure patterns.

    Triggers when tool success rate drops, certain tools consistently error,
    or timeout patterns emerge.
    """

    def __init__(
        self,
        name: str,
        *,
        min_tool_success: float = 0.8,
        max_error_tools: int | None = None,
        timeout_threshold_ms: int | None = None,
        window_size: int = 10,
        enabled: bool = True,
    ) -> None:
        super().__init__(name, enabled=enabled)
        self.min_tool_success = min_tool_success
        self.max_error_tools = max_error_tools
        self.timeout_threshold_ms = timeout_threshold_ms
        self.window_size = window_size
        self._history: dict[str, list[dict[str, Any]]] = {}

    def _check(self, event: ProactiveEvent) -> tuple[QualitySignal, ...]:
        if event.kind != "tool_call":
            return ()

        payload = event.payload
        tool_name = payload.get("tool", "unknown")
        ok = bool(payload.get("ok", True))
        duration_ms = float(payload.get("duration_ms", 0))

        if tool_name not in self._history:
            self._history[tool_name] = []
        self._history[tool_name].append({
            "ok": ok,
            "duration_ms": duration_ms,
        })
        if len(self._history[tool_name]) > self.window_size:
            self._history[tool_name].pop(0)

        signals: list[QualitySignal] = []

        # Check individual tool success rate
        tool_history = self._history.get(tool_name, [])
        if len(tool_history) >= 3:
            success_rate = sum(1 for h in tool_history if h["ok"]) / len(tool_history)
            if success_rate < self.min_tool_success:
                signals.append(QualitySignal(
                    id=f"{self.name}-tool-failure",
                    severity=SignalSeverity.WARNING,
                    title=f"Tool failure pattern: {tool_name} success rate {success_rate:.0%}",
                    description=f"Tool {tool_name} has been failing frequently ({success_rate:.0%} success over "
                                f"{len(tool_history)} calls). Check tool configuration or dependencies.",
                    metric_name="tool_success_rate",
                    metric_value=success_rate,
                    baseline_value=self.min_tool_success,
                    turn_ids=[event.id],
                    suggested_action="investigate",
                    auto_approvable=False,
                ))

            # Check timeout patterns
            if self.timeout_threshold_ms:
                timed_out = sum(1 for h in tool_history if h["duration_ms"] > self.timeout_threshold_ms)
                if timed_out > 0:
                    signals.append(QualitySignal(
                        id=f"{self.name}-tool-timeout",
                        severity=SignalSeverity.WARNING,
                        title=f"Tool timeout pattern: {tool_name} exceeded {self.timeout_threshold_ms}ms",
                        description=f"{timed_out} of {len(tool_history)} {tool_name} calls exceeded "
                                    f"{self.timeout_threshold_ms}ms timeout.",
                        metric_name="tool_timeout_count",
                        metric_value=timed_out / len(tool_history),
                        baseline_value=0.0,
                        turn_ids=[event.id],
                        suggested_action="investigate",
                        auto_approvable=False,
                    ))

        # Check for too many error tools overall
        if self.max_error_tools is not None:
            error_tools = [name for name, history in self._history.items()
                          if len(history) >= 3 and sum(1 for h in history if not h["ok"]) / len(history) < self.min_tool_success]
            if len(error_tools) > self.max_error_tools:
                signals.append(QualitySignal(
                    id=f"{self.name}-multiple-tool-failures",
                    severity=SignalSeverity.CRITICAL,
                    title=f"Multiple tool failures detected ({len(error_tools)} tools)",
                   _description=f"{len(error_tools)} tools are experiencing frequent failures. "
                                f"Check infrastructure and dependencies.",
                    metric_name="error_tool_count",
                    metric_value=len(error_tools),
                    baseline_value=self.max_error_tools,
                    turn_ids=[event.id],
                    suggested_action="investigate",
                    auto_approvable=False,
                ))

        return tuple(signals)