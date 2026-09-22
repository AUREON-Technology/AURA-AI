"""Proactive Intelligence Loop: event → observe → detect → reason → prioritize → approve → execute → verify → learn.

The central orchestration engine that:
1. Ingests ProactiveEvent s from the kernel/observability layer
2. Runs registered DetectionRule s to produce QualitySignal s
3. Prioritizes signals by severity and impact
4. Manages the approval flow (auto-approvable vs human-in-the-loop)
5. Executes automatic remediation actions
6. Verifies remediation effectiveness
7. Feeds learning signals back to the MLOps/finetuning pipeline
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Sequence

from .detector import DetectionRule, QualitySignal, SignalSeverity
from .event import ProactiveEvent

__all__ = [
    "ProactiveLoop",
    "ApprovalDecision",
    "ExecutionResult",
    "RemediationAction",
]


@dataclass
class ApprovalDecision:
    """The result of an approval flow for a quality signal."""
    signal_id: str
    approved: bool
    approved_by: str | None = None  # "system" or user/actor identifier
    approved_at: float | None = None
    conditions: dict[str, Any] = field(default_factory=dict)
    rejection_reason: str | None = None


@dataclass
class ExecutionResult:
    """Result of executing a remediation action."""
    signal_id: str
    success: bool
    output: dict[str, Any] = field(default_factory=dict)
    executed_at: float | None = None
    error: str | None = None


@dataclass
class RemediationAction:
    """A remediation action to resolve a quality signal."""
    id: str
    action_type: str  # "index_refresh", "model_retrain", "config_update", etc.
    description: str
    auto_approvable: bool
    execute: Callable[..., Any]  # async callable that returns any
    check_result: Callable[[Any], bool] | None = None  # optional post-check


class ProactiveLoop:
    """The continuous intelligence loop.

    Orchestrates the full event→detect→approve→execute→verify→learn cycle.

    Signal flow:
    1. Events enter via ingest_event()
    2. Detection rules evaluate each event → QualitySignal s
    3. Signals are queued and prioritized
    4. Auto-approvable signals are executed immediately
    5. Non-auto-approvable signals go through human/ policy approval
    6. Executed remediation is verified for effectiveness
    7. Outcome feeds back into the system state for learning
    """

    def __init__(
        self,
        *,
        rules: Sequence[DetectionRule] | None = None,
        auto_approve: bool = True,
        max_concurrent_remediations: int = 3,
        approval_callback: Callable[[QualitySignal], ApprovalDecision] | None = None,
    ) -> None:
        self.rules = rules or []
        self.auto_approve = auto_approve
        self.max_concurrent_remediations = max_concurrent_remediations
        self.approval_callback = approval_callback

        # Event and signal state
        self._event_history: list[ProactiveEvent] = []
        self._max_event_history = 1000
        self._pending_signals: list[QualitySignal] = []
        self._active_remediations: dict[str, RemediationAction] = {}
        self._signal_counter = 0

        # Metrics about the loop itself
        self._signals_generated_total = 0
        self._signals_approved_total = 0
        self._signals_auto_approved_total = 0
        self._remediations_executed_total = 0
        self._remediations_succeeded_total = 0

    async def ingest_event(self, event: ProactiveEvent) -> None:
        """Feed one event into the loop for detection and potential signal generation."""
        self._event_history.append(event)
        if len(self._event_history) > self._max_event_history:
            self._event_history.pop(0)

        # Run all detection rules
        for rule in self.rules:
            signals = rule.check(event)
            for signal in signals:
                self._signals_generated_total += 1
                signal.turn_ids = tuple(
                    e.id for e in self._event_history[-20:] if e.kind in ("turn_complete", "retrieval", "tool_call")
                )
                # Assign signal ID if not set
                if not signal.id:
                    signal.id = f"signal-{int(time.time()*1000)}-{self._signal_counter:06d}"
                    self._signal_counter += 1

                # Prioritize and queue
                self._pending_signals.append(signal)

                # Immediate auto-approve if configured
                if self.auto_approve and signal.auto_approvable:
                    decision = await self._auto_approve(signal)
                    if decision.approved:
                        await self._execute_remediation(signal)

        # Prune old pending signals (keep last 500)
        if len(self._pending_signals) > 500:
            self._pending_signals = self._pending_signals[-500:]

    async def _auto_approve(self, signal: QualitySignal) -> ApprovalDecision:
        """Auto-approve a signal that marks auto_approvable=True."""
        if self.approval_callback:
            decision = self.approval_callback(signal)
        else:
            # Default: auto-approve if marked
            decision = ApprovalDecision(
                signal_id=signal.id,
                approved=True,
                approved_by="system",
                approved_at=time.time(),
            )
        return decision

    async def _approve_signal(self, signal: QualitySignal) -> ApprovalDecision:
        """Run the approval callback or default decision."""
        if self.approval_callback:
            decision = self.approval_callback(signal)
        else:
            # Default: auto-approve only if auto_approvable, otherwise block
            decision = ApprovalDecision(
                signal_id=signal.id,
                approved=signal.auto_approvable,
                approved_by="system" if signal.auto_approvable else "policy",
                approved_at=time.time() if signal.auto_approvable else None,
                rejection_reason="Requires human approval" if not signal.auto_approvable else None,
            )
        return decision

    async def _execute_remediation(self, signal: QualitySignal) -> ExecutionResult:
        """Execute remediation for an approved signal."""
        # Find matching pending signal
        pending = next((s for s in self._pending_signals if s.id == signal.id), None)
        if not pending:
            return ExecutionResult(
                signal_id=signal.id,
                success=False,
                error="Signal not found in pending queue",
            )

        # Find matching remediation action
        action = self._find_remediation_action(signal)
        if not action:
            return ExecutionResult(
                signal_id=signal.id,
                success=False,
                error=f"No remediation action configured for signal {signal.id}",
            )

        # Concurrency check
        active_count = len(self._active_remediations)
        if active_count >= self.max_concurrent_remediations:
            return ExecutionResult(
                signal_id=signal.id,
                success=False,
                error=f"Max concurrent remediations ({self.max_concurrent_remediations}) reached",
            )

        # Execute the action
        self._active_remediations[signal.id] = action
        started = time.time()

        try:
            result = await action.execute()
            succeeded = True
            output = {"result": result} if result else {}
        except Exception as e:
            succeeded = False
            output = {}
            error = str(e)
        finally:
            self._active_remediations.pop(signal.id, None)

        self._remediations_executed_total += 1
        if succeeded:
            self._remediations_succeeded_total += 1

        return ExecutionResult(
            signal_id=signal.id,
            success=succeeded,
            output=output,
            executed_at=time.time(),
            error=error,
        )

    def _find_remediation_action(self, signal: QualitySignal) -> RemediationAction | None:
        """Find a remediation action matching the signal's suggested_action."""
        # Map suggested actions to concrete remediation types
        action_map = {
            "index_refresh": RemediationAction(
                id=f"refresh-{signal.id}",
                action_type="index_refresh",
                description="Refresh/rebuild the knowledge index",
                auto_approvable=True,
                execute=self._default_index_refresh,
            ),
            "investigate": RemediationAction(
                id=f"investigate-{signal.id}",
                action_type="investigate",
                description="Manual investigation required",
                auto_approvable=False,
                execute=self._default_noop,
            ),
            "retrain": RemediationAction(
                id=f"retrain-{signal.id}",
                action_type="model_retrain",
                description="Trigger model fine-tuning retrain",
                auto_approvable=True,
                execute=self._default_retrain,
            ),
            "monitor": RemediationAction(
                id=f"monitor-{signal.id}",
                action_type="monitor",
                description="Continue monitoring, no immediate action",
                auto_approvable=True,
                execute=self._default_noop,
            ),
        }

        action_type = signal.suggested_action
        return action_map.get(action_type, None)

    async def _default_index_refresh(self) -> Any:
        """Default index refresh action - rebuild the knowledge index."""
        # This would integrate with the knowledge engine's refresh pipeline
        await asyncio.sleep(0.1)  # Simulate work
        return {"status": "index_refresh_completed", "new_generation": int(time.time())}

    async def _default_retrain(self) -> Any:
        """Default model retrain action - trigger SFT/LoRA training."""
        await asyncio.sleep(0.1)  # Simulate work
        return {"status": "retrain_triggered", "pipeline": "sft_lora"}

    async def _default_noop(self) -> None:
        """No-op action for cases that don't need execution."""
        await asyncio.sleep(0.01)

    async def verify_remediation(self, signal_id: str, *, check_result: Callable[[Any], bool]) -> bool:
        """Verify that a remediation action resolved the quality issue.

        Runs a post-check against the system state to confirm the signal's metric
        has recovered to acceptable levels.
        """
        # Look for recent execution result
        # In a full implementation, this would query the system state
        # For now, just run the check callback
        return check_result(signal_id)

    def get_stats(self) -> dict[str, Any]:
        """Get loop execution statistics."""
        return {
            "signals_generated": self._signals_generated_total,
            "signals_approved": self._signals_approved_total,
            "signals_auto_approved": self._signals_auto_approved_total,
            "remediations_executed": self._remediations_executed_total,
            "remediations_succeeded": self._remediations_succeeded_total,
            "pending_signals": len(self._pending_signals),
            "active_remediations": len(self._active_remediations),
            "event_history_size": len(self._event_history),
        }

    async def run_cycle(self, events: list[ProactiveEvent]) -> dict[str, Any]:
        """Run one full loop cycle over a batch of events.

        Convenience method for batch processing.
        """
        for event in events:
            await self.ingest_event(event)

        # Process any signals that need approval/execution
        await self._process_pending_signals()

        return self.get_stats()

    async def _process_pending_signals(self) -> None:
        """Process signals in the pending queue that need approval or execution."""
        # Separate auto-approvable from manual
        auto_signals = [s for s in self._pending_signals if s.auto_approvable]
        manual_signals = [s for s in self._pending_signals if not s.auto_approvable]

        # Auto-approve and execute
        for signal in auto_signals:
            decision = await self._auto_approve(signal)
            if decision.approved:
                result = await self._execute_remediation(signal)
                # Remove from pending
                self._pending_signals = [s for s in self._pending_signals if s.id != signal.id]

        # Manual signals need approval callback
        for signal in manual_signals:
            if self.approval_callback:
                decision = await self._approve_signal(signal)
                if decision.approved:
                    result = await self._execute_remediation(signal)
                    self._pending_signals = [s for s in self._pending_signals if s.id != signal.id]


# Convenience function to build a default proactive loop for AURA
def build_proactive_loop(*,
                         approval_callback: Callable[[QualitySignal], ApprovalDecision] | None = None) -> ProactiveLoop:
    """Build a ProactiveLoop with default detection rules for AURA."""
    from .detector import RetrievalQualityRule, AnswerQualityRule, ToolSuccessRule

    rules = [
        RetrievalQualityRule("retrieval_quality", min_recall=0.3, min_precision=0.3),
        AnswerQualityRule("answer_quality", min_token_f1=0.5, min_groundedness=0.6),
        ToolSuccessRule("tool_success", min_tool_success=0.8),
    ]

    loop = ProactiveLoop(
        rules=rules,
        auto_approve=True,
        approval_callback=approval_callback,
    )
    return loop