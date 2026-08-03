"""Context engine: budgeting, ranking, compression, assembly.

This is where the platform decides what the model actually sees. Four responsibilities:

**Budget.** Derive a token budget from the target provider's context window, reserving output
space. Nothing today counts tokens at all, so prompts grow until a provider rejects them.

**Rank.** Order candidates by relevance × priority. Pinned instructions always survive.

**Compress.** When history exceeds its allocation, replace older turns with a running summary
rather than truncating mid-conversation.

**Assemble.** Produce a :class:`~aura_core.domain.context.ContextBundle` and render it with
untrusted content inside nonce-delimited fences (ADR-006) -- the structural fix for SEC-02,
where retrieved documents are concatenated straight into the system instruction.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Sequence

from ..domain.context import ContextBudget, ContextBundle, ContextItem, ContextSection, pack
from ..domain.knowledge import RetrievalResult
from ..domain.memory import MemoryHit
from ..domain.messages import Conversation, Message, Role, TrustChannel
from ..observability.logging import get_logger
from .tokenizer import Tokenizer, get_tokenizer

__all__ = ["ContextEngine", "ContextRequest", "AssembledContext"]

logger = get_logger("context")


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Everything available to build a turn's context from."""

    system_prompt: str
    user_message: str
    history: Conversation = Conversation()
    memories: Sequence[MemoryHit] = ()
    retrieval: RetrievalResult | None = None
    tool_results: Sequence[tuple[str, str]] = ()
    scratchpad: Sequence[str] = ()
    history_summary: str = ""
    context_window: int = 8192
    max_output_tokens: int = 1024
    memory_token_budget: int | None = None


@dataclass(frozen=True, slots=True)
class AssembledContext:
    """The finished context, ready to become a :class:`Conversation`."""

    bundle: ContextBundle
    conversation: Conversation
    system_text: str
    citations: tuple[str, ...]
    prompt_tokens: int
    duration_ms: float
    dropped: int = 0
    truncated: int = 0

    @property
    def utilisation(self) -> float:
        return self.bundle.utilisation


class ContextEngine:
    def __init__(
        self,
        *,
        tokenizer: Tokenizer | None = None,
        metrics: Any = None,
        history_turns: int = 12,
        min_relevance: float = 0.0,
    ) -> None:
        self._tokenizer = tokenizer or get_tokenizer()
        self._metrics = metrics
        self._history_turns = history_turns
        self._min_relevance = min_relevance

    # -- budgeting ---------------------------------------------------------------------

    def budget_for(self, request: ContextRequest) -> ContextBudget:
        """Derive the budget from the provider's window.

        Reserves output space plus a 5 % margin. The margin absorbs tokenizer estimation error
        (``HeuristicTokenizer`` is biased high but not exact) and per-provider framing
        differences -- without it, a prompt sized to exactly the window overflows on the vendor
        whose message framing costs two more tokens than ours.
        """
        usable = int(request.context_window * 0.95)
        reserved = min(request.max_output_tokens, max(256, usable // 4))
        return ContextBudget(total=usable, reserved_for_output=reserved)

    # -- candidate construction --------------------------------------------------------

    def _instruction_items(self, request: ContextRequest, bundle_nonce_rules: str) -> list[ContextItem]:
        """The system prompt and the fence rules -- pinned, never evicted.

        Priority 100+ bypasses section caps in :func:`pack`. If the operator's instructions can
        be dropped to make room for a document, the safety rules go with them.
        """
        items = [
            ContextItem(
                content=request.system_prompt,
                section=ContextSection.INSTRUCTIONS,
                channel=TrustChannel.SYSTEM,
                priority=200,
                relevance=1.0,
                tokens=self._tokenizer.count(request.system_prompt),
                item_id="system_prompt",
            )
        ]
        if bundle_nonce_rules:
            items.append(
                ContextItem(
                    content=bundle_nonce_rules,
                    section=ContextSection.INSTRUCTIONS,
                    channel=TrustChannel.SYSTEM,
                    priority=210,
                    relevance=1.0,
                    tokens=self._tokenizer.count(bundle_nonce_rules),
                    item_id="fence_rules",
                )
            )
        return items

    def _memory_items(self, request: ContextRequest) -> list[ContextItem]:
        items = []
        for hit in request.memories:
            record = hit.record
            items.append(
                ContextItem(
                    content=record.content,
                    section=ContextSection.MEMORY,
                    channel=TrustChannel.DATA,
                    relevance=hit.score,
                    # Memory is generally more valuable per token than a document chunk: it is
                    # short, specific to this user, and cannot be re-derived from retrieval.
                    priority=20,
                    tokens=self._tokenizer.count(record.content),
                    provenance=f"memory:{record.kind}:{record.id.value[:8]}",
                    item_id=record.id.value,
                    metadata={"kind": str(record.kind), "confidence": record.confidence},
                )
            )
        return items

    def _knowledge_items(self, request: ContextRequest) -> list[ContextItem]:
        if request.retrieval is None:
            return []
        items = []
        for index, retrieved in enumerate(request.retrieval.chunks):
            if retrieved.quarantined:
                # Quarantined chunks never reach the model, even fenced. The taint flag on the
                # turn is what the rest of the pipeline reacts to.
                continue
            content = retrieved.chunk.content
            items.append(
                ContextItem(
                    content=content,
                    section=ContextSection.KNOWLEDGE,
                    channel=TrustChannel.DATA,
                    relevance=retrieved.score,
                    # Rank order is a tiebreak within equal relevance, keeping the reranker's
                    # ordering visible in the final prompt.
                    priority=10 - min(index, 9),
                    tokens=self._tokenizer.count(content),
                    provenance=retrieved.citation(),
                    item_id=retrieved.chunk.id.value,
                )
            )
        return items

    def _tool_items(self, request: ContextRequest) -> list[ContextItem]:
        return [
            ContextItem(
                content=result,
                section=ContextSection.TOOL_OUTPUT,
                channel=TrustChannel.DATA,
                # Tool output is the freshest information in the turn and usually the reason the
                # tool was called, so it outranks retrieved documents.
                relevance=0.95,
                priority=30,
                tokens=self._tokenizer.count(result),
                provenance=f"tool:{name}",
                item_id=f"tool:{name}:{index}",
            )
            for index, (name, result) in enumerate(request.tool_results)
        ]

    def _scratchpad_items(self, request: ContextRequest) -> list[ContextItem]:
        return [
            ContextItem(
                content=note,
                section=ContextSection.SCRATCHPAD,
                channel=TrustChannel.SYSTEM,
                relevance=0.9,
                priority=25,
                tokens=self._tokenizer.count(note),
                provenance="working_memory",
                item_id=f"scratch:{index}",
            )
            for index, note in enumerate(request.scratchpad)
        ]

    # -- history compression ------------------------------------------------------------

    def _compress_history(
        self, request: ContextRequest, allowance: int
    ) -> tuple[Conversation, str, int]:
        """Fit history into ``allowance`` tokens.

        Strategy: keep the most recent turns verbatim, fold everything older into the running
        summary. Recent turns carry the actual conversational state (what "it" refers to, what
        was just agreed); older turns carry facts, which survive summarisation.

        Returns ``(kept_conversation, summary_text, dropped_turn_count)``.
        """
        messages = [m for m in request.history if m.role is not Role.SYSTEM]
        if not messages:
            return Conversation(), request.history_summary, 0

        kept: list[Message] = []
        used = 0
        # Walk backwards so the newest turns win the budget.
        for message in reversed(messages):
            cost = self._tokenizer.count(message.content) + 4
            if used + cost > allowance and kept:
                break
            kept.append(message)
            used += cost
        kept.reverse()

        dropped = len(messages) - len(kept)
        summary = request.history_summary
        if dropped > 0 and not summary:
            # No summary was supplied for the turns being dropped. Say so explicitly rather
            # than letting the model believe the conversation started where the window does.
            summary = f"[{dropped} earlier turn(s) omitted for length]"
        return Conversation.of(kept), summary, dropped

    # -- assembly ----------------------------------------------------------------------

    def assemble(self, request: ContextRequest) -> AssembledContext:
        started = time.perf_counter()
        budget = self.budget_for(request)

        # The nonce is generated with the bundle, and the fence rules must reference it, so an
        # empty bundle is created first purely to obtain one.
        seed = ContextBundle.empty()
        fence_rules = seed.fence_instructions()

        candidates: list[ContextItem] = []
        candidates += self._instruction_items(request, fence_rules)
        candidates += self._memory_items(request)
        candidates += self._knowledge_items(request)
        candidates += self._tool_items(request)
        candidates += self._scratchpad_items(request)

        if self._min_relevance > 0:
            candidates = [
                c for c in candidates
                if c.priority >= 100 or c.relevance >= self._min_relevance
            ]

        # History is packed separately: it is a sequence, not a set of independent items, so
        # greedy density packing would happily drop the middle of a conversation.
        history_allowance = budget.cap_for(ContextSection.HISTORY)
        history, summary, dropped_turns = self._compress_history(request, history_allowance)

        if summary:
            candidates.append(
                ContextItem(
                    content=f"Earlier in this conversation: {summary}",
                    section=ContextSection.HISTORY,
                    channel=TrustChannel.SYSTEM,
                    relevance=0.8,
                    priority=40,
                    tokens=self._tokenizer.count(summary),
                    provenance="episodic_summary",
                    item_id="history_summary",
                )
            )

        # Reserve what history will actually consume so the packer cannot spend it.
        history_tokens = sum(self._tokenizer.count(m.content) + 4 for m in history)
        user_tokens = self._tokenizer.count(request.user_message) + 4
        packing_budget = ContextBudget(
            total=max(
                budget.reserved_for_output + 256,
                budget.total - history_tokens - user_tokens,
            ),
            reserved_for_output=budget.reserved_for_output,
            section_caps=budget.section_caps,
        )

        bundle = pack(candidates, packing_budget)
        # Carry the seed's nonce onto the packed bundle so the rendered fences match the rules
        # text that was already pinned into the instruction items.
        bundle = ContextBundle(
            items=bundle.items,
            dropped=bundle.dropped,
            truncated=bundle.truncated,
            budget_total=bundle.budget_total,
            tokens_used=bundle.tokens_used,
            fence_nonce=seed.fence_nonce,
        )

        system_text = bundle.render(
            include_sections=(
                ContextSection.INSTRUCTIONS,
                ContextSection.MEMORY,
                ContextSection.KNOWLEDGE,
                ContextSection.TOOL_OUTPUT,
                ContextSection.SCRATCHPAD,
            )
        )
        history_text = bundle.render(include_sections=(ContextSection.HISTORY,))

        messages: list[Message] = [Message.system(system_text)]
        if history_text:
            messages.append(Message.system(history_text))
        messages.extend(history)
        messages.append(Message.user(request.user_message))

        conversation = Conversation.of(messages)
        prompt_tokens = self._tokenizer.count_messages(messages)
        duration_ms = (time.perf_counter() - started) * 1000.0

        if self._metrics is not None and bundle.dropped:
            logger.info(
                "context items dropped for budget",
                extra={"dropped": len(bundle.dropped), "used": bundle.tokens_used,
                       "budget": bundle.budget_total},
            )

        return AssembledContext(
            bundle=bundle,
            conversation=conversation,
            system_text=system_text,
            citations=bundle.citations,
            prompt_tokens=prompt_tokens,
            duration_ms=duration_ms,
            dropped=len(bundle.dropped) + dropped_turns,
            truncated=len(bundle.truncated),
        )

    # -- helpers -----------------------------------------------------------------------

    def count(self, text: str) -> int:
        return self._tokenizer.count(text)

    @property
    def tokenizer_name(self) -> str:
        return self._tokenizer.name

    @property
    def counts_are_exact(self) -> bool:
        return self._tokenizer.exact
