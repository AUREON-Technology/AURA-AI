"""Guard orchestration (ADR-006).

The pipeline runs guards in order, threads sanitised text between them, aggregates verdicts
by strictest-wins, and emits metrics and audit events. Guards decide *what they see*; the
pipeline decides *what happens*.

Ordering is deliberate and not merely stylistic:

1. ``SizeGuard`` first -- regex-scanning a 10 MB payload is itself a denial of service.
2. ``EncodingGuard`` second -- it normalises the text every later pattern is matched against.
   Running injection detection before NFKC folding makes every pattern trivially bypassable
   with full-width characters.
3. Detection guards last, on clean, bounded text.

Short-circuiting: a ``BLOCK`` stops the chain. There is nothing useful to learn from running
four more guards over text that is already refused, and the latency is paid on every request.
"""

from __future__ import annotations

import time
from typing import Any, Mapping, Sequence

from ..domain.errors import SafetyViolationError
from ..domain.events import SafetyTriggered
from ..domain.identifiers import TurnId, UserId
from ..domain.safety import GuardAction, GuardStage, GuardVerdict, SafetyReport
from ..observability.logging import get_logger
from .guards import (
    Guard,
    SecretGuard,
    default_context_guards,
    default_input_guards,
    default_output_guards,
)

__all__ = ["SafetyPipeline", "SafetyDecision"]

logger = get_logger("safety")


class SafetyDecision:
    """What the pipeline concluded about one piece of text."""

    __slots__ = ("report", "text", "allowed", "tainted")

    def __init__(self, report: SafetyReport, *, allowed: bool, tainted: bool) -> None:
        self.report = report
        self.text = report.text
        self.allowed = allowed
        # True when a data-channel item was quarantined. Propagates to the ExecutionContext so
        # side-effecting tools require confirmation for the rest of the turn.
        self.tainted = tainted

    def __bool__(self) -> bool:
        return self.allowed


class SafetyPipeline:
    """Runs the guard chain for each stage."""

    def __init__(
        self,
        *,
        input_guards: Sequence[Guard] | None = None,
        context_guards: Sequence[Guard] | None = None,
        output_guards: Sequence[Guard] | None = None,
        enabled: bool = True,
        metrics: Any = None,
        event_bus: Any = None,
        audit: Any = None,
    ) -> None:
        self._enabled = enabled
        self._by_stage: dict[GuardStage, tuple[Guard, ...]] = {
            GuardStage.INPUT: tuple(input_guards if input_guards is not None else default_input_guards()),
            GuardStage.CONTEXT: tuple(
                context_guards if context_guards is not None else default_context_guards()
            ),
            # Tool output is third-party content (the context chain: size, encoding, injection)
            # AND may carry whatever the tool read -- a config file, an env dump, a connected
            # system's record. ``SecretGuard`` declares this stage but was never in the chain, so
            # credentials in a tool result reached the model and the transcript. It redacts
            # rather than blocks: the rest of the output is still what the model asked for.
            GuardStage.TOOL_RESULT: tuple(
                context_guards if context_guards is not None else default_context_guards()
            ) + (SecretGuard(block=False),),
            GuardStage.OUTPUT: tuple(
                output_guards if output_guards is not None else default_output_guards()
            ),
        }
        self._metrics = metrics
        self._events = event_bus
        self._audit = audit

    @classmethod
    def from_settings(cls, settings: Any, *, metrics: Any = None, event_bus: Any = None,
                      audit: Any = None) -> "SafetyPipeline":
        """Build from :class:`~aura_core.config.settings.SafetySettings`."""
        return cls(
            enabled=settings.enabled,
            input_guards=default_input_guards(
                max_chars=settings.max_input_chars,
                injection_threshold=settings.injection_threshold,
                jailbreak_threshold=settings.jailbreak_threshold,
            )
            if settings.input_guards_enabled
            else [],
            context_guards=default_context_guards(
                max_chars=settings.max_input_chars,
                injection_threshold=settings.injection_threshold,
            )
            if settings.context_guards_enabled
            else [],
            output_guards=default_output_guards(
                redact_pii=settings.pii_redact_in_output,
                block_secrets=settings.block_on_secret_leak,
                groundedness_min=settings.groundedness_min,
            )
            if settings.output_guards_enabled
            else [],
            metrics=metrics,
            event_bus=event_bus,
            audit=audit,
        )

    def guards(self, stage: GuardStage) -> tuple[Guard, ...]:
        """The guards configured for ``stage`` (read-only; used by the EV-7 guard evaluation)."""
        return self._by_stage.get(stage, ())

    # -- core ---------------------------------------------------------------------------

    async def evaluate(
        self,
        text: str,
        *,
        stage: GuardStage,
        context: Mapping[str, Any] | None = None,
        user_id: UserId | None = None,
        turn_id: TurnId | None = None,
    ) -> SafetyDecision:
        """Run the chain for one stage and return the decision.

        ``async`` even though current guards are synchronous: a classifier-backed guard will be
        an HTTP call, and changing this signature later would touch every call site.
        """
        if not self._enabled:
            return SafetyDecision(SafetyReport.clean(stage, text), allowed=True, tainted=False)

        started = time.perf_counter()
        guards = [g for g in self._by_stage.get(stage, ()) if g.applies_to(stage)]
        working = text
        verdicts: list[GuardVerdict] = []
        ctx = dict(context or {})

        for guard in guards:
            verdict = guard.evaluate(working, stage=stage, context=ctx)
            verdicts.append(verdict)
            self._observe(verdict, stage)

            if verdict.action is GuardAction.REDACT and verdict.sanitised is not None:
                # Thread the cleaned text forward so later guards see normalised input.
                working = verdict.sanitised
            elif verdict.action is GuardAction.BLOCK:
                break
            elif verdict.action is GuardAction.QUARANTINE:
                # No point scanning content that will be dropped.
                break

        report = SafetyReport(
            stage=stage,
            verdicts=tuple(verdicts),
            text=working,
            original_text=text,
            duration_ms=(time.perf_counter() - started) * 1000.0,
        )
        if self._metrics is not None:
            self._metrics.guard_duration.labels(stage=str(stage)).observe(report.duration_ms / 1000.0)

        await self._emit(report, user_id=user_id, turn_id=turn_id)

        allowed = report.action not in (GuardAction.BLOCK, GuardAction.QUARANTINE)
        tainted = report.action is GuardAction.QUARANTINE
        return SafetyDecision(report, allowed=allowed, tainted=tainted)

    async def guard_input(
        self, text: str, *, user_id: UserId | None = None, turn_id: TurnId | None = None
    ) -> SafetyDecision:
        """Check the user's own message.

        Raises :class:`SafetyViolationError` on a block, because there is no meaningful way to
        continue a turn whose input was refused.
        """
        decision = await self.evaluate(
            text, stage=GuardStage.INPUT, user_id=user_id, turn_id=turn_id
        )
        if decision.report.blocked:
            raise SafetyViolationError(
                "the request was blocked by a safety guard",
                guard=decision.report.blocking_guard or "unknown",
                action="block",
                details={"reasons": list(decision.report.reasons())},
            )
        return decision

    async def guard_context_items(
        self,
        items: Sequence[tuple[str, str]],
        *,
        user_id: UserId | None = None,
        turn_id: TurnId | None = None,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]], bool]:
        """Scan retrieved chunks and memories.

        Returns ``(accepted, quarantined, tainted)`` where each entry is
        ``(provenance, text[, reason])``.

        **Quarantine-and-continue, not reject-the-turn.** A poisoned document in a knowledge
        base must not be able to deny service to every query that happens to retrieve it. The
        chunk is dropped, the turn is tainted, and the answer is produced from what remains.
        """
        accepted: list[tuple[str, str]] = []
        quarantined: list[tuple[str, str, str]] = []
        tainted = False

        for provenance, text in items:
            decision = await self.evaluate(
                text,
                stage=GuardStage.CONTEXT,
                context={"provenance": provenance},
                user_id=user_id,
                turn_id=turn_id,
            )
            if decision.report.quarantined or decision.report.blocked:
                tainted = True
                quarantined.append(
                    (provenance, text, ", ".join(decision.report.reasons()) or "quarantined")
                )
                continue
            accepted.append((provenance, decision.text))

        if tainted and self._metrics is not None:
            self._metrics.turns_tainted_total.inc()
        return accepted, quarantined, tainted

    async def guard_tool_result(
        self, text: str, *, tool: str, user_id: UserId | None = None, turn_id: TurnId | None = None
    ) -> SafetyDecision:
        """Tool output is third-party content and gets the same treatment as a document.

        A web-search tool returning an attacker-controlled page is the single most direct
        injection path in any agent, and it is exactly the path the legacy design leaves open.
        """
        return await self.evaluate(
            text,
            stage=GuardStage.TOOL_RESULT,
            context={"tool": tool},
            user_id=user_id,
            turn_id=turn_id,
        )

    async def guard_output(
        self,
        text: str,
        *,
        system_prompt: str = "",
        citations_text: Sequence[str] = (),
        user_id: UserId | None = None,
        turn_id: TurnId | None = None,
    ) -> SafetyDecision:
        """Check the model's answer before it reaches the user.

        Raises on a block (a credential or the system prompt in the output); returns redacted
        text otherwise.
        """
        decision = await self.evaluate(
            text,
            stage=GuardStage.OUTPUT,
            context={"system_prompt": system_prompt, "citations_text": list(citations_text)},
            user_id=user_id,
            turn_id=turn_id,
        )
        if decision.report.blocked:
            raise SafetyViolationError(
                "the response was withheld by a safety guard",
                guard=decision.report.blocking_guard or "unknown",
                action="block",
                details={"reasons": list(decision.report.reasons())},
            )
        return decision

    # -- reporting ----------------------------------------------------------------------

    def _observe(self, verdict: GuardVerdict, stage: GuardStage) -> None:
        if self._metrics is None:
            return
        self._metrics.guard_verdicts_total.labels(
            guard=verdict.guard, stage=str(stage), action=str(verdict.action)
        ).inc()
        if verdict.guard == "groundedness":
            score = verdict.metadata.get("groundedness")
            if score is not None:
                self._metrics.groundedness_score.observe(float(score))
            ungrounded = int(verdict.metadata.get("ungrounded_sentences", 0) or 0)
            if ungrounded:
                self._metrics.ungrounded_claims_total.inc(ungrounded)

    async def _emit(
        self, report: SafetyReport, *, user_id: UserId | None, turn_id: TurnId | None
    ) -> None:
        """Publish events and audit records for triggered guards.

        Never raises: a failing audit sink must not fail a user's request. It is logged and
        counted instead, which is visible without being fatal.
        """
        triggered = report.triggered
        if not triggered:
            return

        for verdict in triggered:
            # Log the fact, never the content -- the detected span would otherwise put the very
            # payload we are defending against into the log pipeline.
            logger.warning(
                "safety guard triggered",
                extra={
                    "guard": verdict.guard,
                    "stage": str(report.stage),
                    "action": str(verdict.action),
                    "reason": verdict.reason,
                    "confidence": round(verdict.confidence, 3),
                },
            )
            if self._events is not None:
                try:
                    await self._events.publish(
                        SafetyTriggered(
                            guard=verdict.guard,
                            stage=str(report.stage),
                            action=str(verdict.action),
                            reason=verdict.reason,
                            user_id=user_id,
                            turn_id=turn_id,
                        )
                    )
                except Exception:  # pragma: no cover - defensive
                    logger.debug("safety event publication failed", exc_info=True)

        if self._audit is not None and report.action >= GuardAction.QUARANTINE:
            from ..ports.runtime import AuditEvent

            try:
                await self._audit.record(
                    AuditEvent(
                        action=f"safety.{report.action}",
                        actor=user_id.value if user_id else "anonymous",
                        resource=str(report.stage),
                        outcome="blocked" if report.blocked else "quarantined",
                        detail={"guards": report.summary()},
                    )
                )
            except Exception:  # pragma: no cover - defensive
                logger.debug("safety audit write failed", exc_info=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def guard_names(self, stage: GuardStage) -> tuple[str, ...]:
        return tuple(g.name for g in self._by_stage.get(stage, ()))
