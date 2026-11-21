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
from dataclasses import dataclass, replace
from typing import Any, Sequence

from ..domain.cag import CagPrefix
from ..domain.errors import ContextOverflowError
from ..domain.caller_context import CallerContext
from ..domain.context import (
    ContextBudget,
    ContextBundle,
    ContextItem,
    ContextSection,
    pack,
    user_facing_citations,
)
from ..domain.goals import GoalSnapshot
from ..domain.knowledge import RetrievalResult
from ..domain.memory import MemoryHit
from ..domain.messages import Conversation, Message, Role, TrustChannel
from ..observability.logging import get_logger
from .tokenizer import Tokenizer, get_tokenizer

__all__ = [
    "ContextEngine",
    "ContextRequest",
    "AssembledContext",
    "DEFAULT_MAX_PROMPT_TOKENS",
    "DEFAULT_HISTORY_TURNS",
    "DEFAULT_KNOWLEDGE_CHUNK_MAX_CHARS",
    "DEFAULT_MAX_KNOWLEDGE_ITEMS",
]

logger = get_logger("context")

#: Hard cap on the assembled completion prompt (system + CAG + RAG + memory + history + user).
#: CPU-only qwen2.5 7B prompt-eval is ~11 tok/s; 400 tokens keeps prefill under ~40s.
DEFAULT_MAX_PROMPT_TOKENS = 400
DEFAULT_HISTORY_TURNS = 4
DEFAULT_KNOWLEDGE_CHUNK_MAX_CHARS = 280
DEFAULT_MAX_KNOWLEDGE_ITEMS = 2
_CLIP_MARKER = " […truncated]"
#: The smallest answer a turn may be left room for before its prompt counts as overflowing
#: the window (KAN-99).
_MIN_OUTPUT_TOKENS = 64
#: Below this, a shortened item is too small to be useful and is dropped instead -- the same
#: threshold `pack()` uses before it truncates rather than drops.
_MIN_TRUNCATED_TOKENS = 24
#: Sections rendered into the leading system message; HISTORY gets its own message.
_SYSTEM_SECTIONS = (
    ContextSection.INSTRUCTIONS,
    ContextSection.CAG,
    ContextSection.MEMORY,
    ContextSection.KNOWLEDGE,
    ContextSection.TOOL_OUTPUT,
    ContextSection.SCRATCHPAD,
)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    """Everything available to build a turn's context from."""

    system_prompt: str
    user_message: str
    history: Conversation = Conversation()
    memories: Sequence[MemoryHit] = ()
    goals: Sequence[GoalSnapshot] = ()
    retrieval: RetrievalResult | None = None
    cag_prefix: CagPrefix | None = None
    tool_results: Sequence[tuple[str, str]] = ()
    scratchpad: Sequence[str] = ()
    history_summary: str = ""
    context_window: int = 8192
    max_output_tokens: int = 1024
    memory_token_budget: int | None = None
    #: None uses the engine default. 0 disables the extra cap (provider window only).
    max_prompt_tokens: int | None = None
    #: Evidence the calling system supplied (AR-5), already guarded by the kernel. DATA only.
    caller_context: Sequence[CallerContext] = ()


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
    #: The answer size to ask for: the request's, clamped to what the context window leaves
    #: after this prompt (KAN-99). 0 means "not computed" (a hand-built value); callers then
    #: use the request's own size.
    max_output_tokens: int = 0
    #: True when even the irreducible prompt exceeds the prefill cap. It still fits the window,
    #: so it is sent; telemetry and logs can say why the turn's prefill ran long.
    over_cap: bool = False
    #: History messages in neither the prompt nor the summary, which the omission marker in
    #: the history message accounts for (KAN-100).
    omitted_messages: int = 0

    @property
    def utilisation(self) -> float:
        return self.bundle.utilisation

    @property
    def instructions_text(self) -> str:
        """Only the operator's instructions -- what the leak guard must compare an answer to.

        ``system_text`` also carries retrieved knowledge and tool output, which an answer is
        *meant* to quote; comparing against it blocks every well-grounded quotation as a
        "system prompt leak" once the prompt budget is large enough to include them.
        """
        return self.bundle.render(include_sections=(ContextSection.INSTRUCTIONS,))

    @property
    def user_citations(self) -> tuple[str, ...]:
        """Document citations for the client; excludes packing/telemetry bookkeeping."""
        return user_facing_citations(self.citations)


class ContextEngine:
    def __init__(
        self,
        *,
        tokenizer: Tokenizer | None = None,
        metrics: Any = None,
        history_turns: int = DEFAULT_HISTORY_TURNS,
        min_relevance: float = 0.0,
        max_prompt_tokens: int = DEFAULT_MAX_PROMPT_TOKENS,
        knowledge_chunk_max_chars: int = DEFAULT_KNOWLEDGE_CHUNK_MAX_CHARS,
        max_knowledge_items: int = DEFAULT_MAX_KNOWLEDGE_ITEMS,
    ) -> None:
        self._tokenizer = tokenizer or get_tokenizer()
        self._metrics = metrics
        self._history_turns = history_turns
        self._min_relevance = min_relevance
        self._max_prompt_tokens = max_prompt_tokens
        self._knowledge_chunk_max_chars = knowledge_chunk_max_chars
        self._max_knowledge_items = max_knowledge_items
        # The tokenizer's own chat framing, learned from it rather than assumed: what an empty
        # conversation costs (reply priming) and what one more empty message adds. With these,
        # a conversation's cost is a sum of per-message costs, each counted once (KAN-105).
        self._priming = self._tokenizer.count_messages(())
        self._message_overhead = (
            self._tokenizer.count_messages((Message.user(""),)) - self._priming
        )
        self._separator = self._tokenizer.count("\n")

    def _message_cost(self, content_tokens: int) -> int:
        return content_tokens + self._message_overhead

    # -- budgeting ---------------------------------------------------------------------

    def budget_for(self, request: ContextRequest) -> ContextBudget:
        """Derive the budget from the provider's window and the prompt cap.

        Reserves output space plus a 5 % margin against the provider window. A separate
        ``max_prompt_tokens`` cap bounds *prefill* independently of the window -- otherwise a
        8k-context local model would still ingest 1k+ token RAG prompts that CPU Ollama cannot
        evaluate before the stream timeout.
        """
        usable, reserved, window_prompt = self._window_limits(request)
        cap = self._effective_prompt_cap(request)
        if cap is None:
            return ContextBudget(total=usable, reserved_for_output=reserved)
        # The cap never exceeds what the window leaves after the output reservation (KAN-99):
        # a large configured cap on a small-window provider used to budget a prompt the model
        # had no room to answer after.
        cap = min(cap, window_prompt)
        # Prefill is the assembled prompt. Output reservation must not consume that cap.
        # Hold ~15 % back for fence wrappers, section headers, and per-message framing, which
        # are added at render time and are not in item.token counts.
        framing = max(48, cap * 15 // 100)
        available = max(64, cap - framing)
        return ContextBudget(total=available + 1, reserved_for_output=1)

    def _effective_prompt_cap(self, request: ContextRequest) -> int | None:
        if request.max_prompt_tokens == 0:
            return None
        if request.max_prompt_tokens is not None:
            return request.max_prompt_tokens
        return self._max_prompt_tokens or None

    @staticmethod
    def _window_limits(request: ContextRequest) -> tuple[int, int, int]:
        """``(usable, reserved_output, window_prompt)`` for the turn's provider window.

        ``usable`` keeps a 5 % margin for tokenizer error; ``window_prompt`` is what is left
        for the prompt once the output reservation is taken out of it.
        """
        usable = int(request.context_window * 0.95)
        reserved = min(request.max_output_tokens, max(256, usable // 4))
        return usable, reserved, max(64, usable - reserved)

    def _prompt_limit(self, request: ContextRequest) -> int:
        """The prompt size assembly aims for: the cap, bounded by the window (KAN-99)."""
        _, _, window_prompt = self._window_limits(request)
        cap = self._effective_prompt_cap(request)
        return min(cap, window_prompt) if cap else window_prompt

    # -- candidate construction --------------------------------------------------------

    def _instruction_items(self, request: ContextRequest) -> list[ContextItem]:
        """The system prompt -- pinned, never evicted.

        Fence rules are not here: a per-turn nonce in instructions would make every prompt
        unique and poison a later KV-prefix cache of the CAG slot (ADR-010). Prefix fence
        rules live inside the CAG section; turn fence rules are injected after it.
        """
        return [
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

    def _cag_items(
        self, request: ContextRequest, *, prefix_fence_rules: str
    ) -> list[ContextItem]:
        """Byte-stable CAG prefix: fence rules + fenced knowledge (ADR-010)."""
        prefix = request.cag_prefix
        if prefix is None or not prefix.text.strip():
            return []
        items: list[ContextItem] = []
        if prefix_fence_rules:
            items.append(
                ContextItem(
                    content=prefix_fence_rules,
                    section=ContextSection.CAG,
                    channel=TrustChannel.SYSTEM,
                    priority=190,
                    relevance=1.0,
                    tokens=self._tokenizer.count(prefix_fence_rules),
                    item_id="cag_fence_rules",
                )
            )
        provenance = "; ".join(prefix.citations) if prefix.citations else "cag"
        items.append(
            ContextItem(
                content=prefix.text,
                section=ContextSection.CAG,
                channel=TrustChannel.DATA,
                priority=180,
                relevance=1.0,
                tokens=prefix.tokens or self._tokenizer.count(prefix.text),
                provenance=provenance,
                item_id=f"cag:{prefix.identity.value[:12]}",
            )
        )
        return items

    def _turn_fence_item(self, fence_rules: str) -> ContextItem:
        """Turn-nonce fence rules, placed after CAG so they do not destabilise the prefix."""
        return ContextItem(
            content=fence_rules,
            section=ContextSection.MEMORY,
            channel=TrustChannel.SYSTEM,
            priority=170,
            relevance=1.0,
            tokens=self._tokenizer.count(fence_rules),
            item_id="turn_fence_rules",
        )

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

    def _goal_items(self, request: ContextRequest) -> list[ContextItem]:
        """Active goals as persistent facts about the user (Phase D2).

        Content is built entirely from :class:`~aura_core.domain.goals.GoalSnapshot`'s plaintext
        fields -- no title or description. Those are ciphertext under the user's data key
        (ADR-032), which the server holds, but ``GoalsPort`` does not decrypt them. A goal a
        model cannot name is still worth knowing the shape of: how urgent, how important, how
        far along.
        """
        items = []
        for goal in request.goals:
            parts = [f"priority={goal.priority}"]
            if goal.category:
                parts.append(f"category={goal.category}")
            if goal.target_date is not None:
                parts.append(f"target_date={goal.target_date.date().isoformat()}")
            parts.append(f"progress={goal.progress_pct}%")
            content = "Active goal (" + ", ".join(parts) + ")"
            items.append(
                ContextItem(
                    content=content,
                    section=ContextSection.MEMORY,
                    channel=TrustChannel.DATA,
                    # Same tier as recalled memory: a persistent fact about the user, not
                    # freshly retrieved and not as valuable per token as an exact preference.
                    relevance=0.6,
                    priority=18,
                    tokens=self._tokenizer.count(content),
                    provenance=f"goal:{goal.id[:8]}",
                    item_id=f"goal:{goal.id}",
                    metadata={"status": goal.status},
                )
            )
        return items

    def _knowledge_items(self, request: ContextRequest) -> list[ContextItem]:
        if request.retrieval is None:
            return []
        pinned_ids = request.cag_prefix.document_ids if request.cag_prefix else frozenset()
        items = []
        kept = 0
        for index, retrieved in enumerate(request.retrieval.chunks):
            if retrieved.quarantined:
                # Quarantined chunks never reach the model, even fenced. The taint flag on the
                # turn is what the rest of the pipeline reacts to.
                continue
            # Dedup: a chunk already in the CAG prefix would waste the 280-char RAG clip.
            if retrieved.chunk.document_id.value in pinned_ids:
                continue
            if kept >= self._max_knowledge_items:
                break
            content = _clip_chars(retrieved.chunk.content, self._knowledge_chunk_max_chars)
            items.append(
                ContextItem(
                    content=content,
                    section=ContextSection.KNOWLEDGE,
                    channel=TrustChannel.DATA,
                    relevance=retrieved.score,
                    # Knowledge outranks memory (20): a retrieved citation is why this turn
                    # retrieved, and on a tight CPU prefill budget memory would otherwise spend
                    # the last tokens and drop the chunk.
                    priority=25 - min(index, 9),
                    tokens=self._tokenizer.count(content),
                    provenance=retrieved.citation(),
                    item_id=retrieved.chunk.id.value,
                )
            )
            kept += 1
        return items

    def _caller_context_items(self, request: ContextRequest) -> list[ContextItem]:
        """Caller-supplied evidence (AR-5): fenced DATA exactly like a retrieved chunk, cited by
        the caller's own id. Not clipped -- the caller bounded it, and a clipped measurement is
        a different measurement -- so on a tight budget an entry is dropped whole, and an entry
        that was dropped is never cited."""
        items = []
        for index, entry in enumerate(request.caller_context):
            content = entry.content()
            items.append(
                ContextItem(
                    content=content,
                    section=ContextSection.KNOWLEDGE,
                    channel=TrustChannel.DATA,
                    relevance=1.0,
                    # Above retrieved knowledge (25): the caller sent these for this very turn.
                    priority=30 - min(index, 9),
                    tokens=self._tokenizer.count(content),
                    provenance=entry.id,
                    item_id=f"ctx:{entry.id}",
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
    ) -> tuple[list[tuple[Message, int]], str, int]:
        """Fit history into ``allowance`` tokens.

        Strategy: keep the most recent turns verbatim, fold everything older into the running
        summary. Recent turns carry the actual conversational state (what "it" refers to, what
        was just agreed); older turns carry facts, which survive summarisation.

        Returns ``(kept, summary_text, dropped_turn_count)``. ``kept`` pairs each message with
        its cost (content plus chat framing), counted here once and reused by every later step
        (KAN-105). Messages outside the window are never counted at all.
        """
        messages = [m for m in request.history if m.role is not Role.SYSTEM]
        if not messages:
            return [], request.history_summary, 0

        dropped_by_window = 0
        if self._history_turns > 0 and len(messages) > self._history_turns:
            dropped_by_window = len(messages) - self._history_turns
            messages = messages[-self._history_turns :]

        kept: list[tuple[Message, int]] = []
        used = 0
        # Walk backwards so the newest turns win the budget.
        for message in reversed(messages):
            cost = self._message_cost(self._tokenizer.count(message.content))
            if used + cost > allowance and kept:
                break
            kept.append((message, cost))
            used += cost
        kept.reverse()

        dropped = dropped_by_window + (len(messages) - len(kept))
        # Every message here is one the session has not yet folded into the summary, so each
        # dropped one is in neither the prompt nor the summary. `assemble` adds a one-line
        # marker that says how many (KAN-100); it used to drop them without a word.
        return kept, request.history_summary, dropped

    # -- assembly ----------------------------------------------------------------------

    def assemble(self, request: ContextRequest) -> AssembledContext:
        started = time.perf_counter()
        budget = self.budget_for(request)

        # Turn nonce for variable DATA; prefix nonce for the CAG slot (ADR-010).
        seed = ContextBundle.empty()
        prefix_nonce = request.cag_prefix.prefix_nonce if request.cag_prefix else None
        seed_with_prefix = ContextBundle(
            fence_nonce=seed.fence_nonce,
            prefix_nonce=prefix_nonce,
        )
        turn_fence_rules = seed_with_prefix.fence_instructions(for_prefix=False)
        prefix_fence_rules = (
            seed_with_prefix.fence_instructions(for_prefix=True) if prefix_nonce else ""
        )

        candidates: list[ContextItem] = []
        candidates += self._instruction_items(request)
        cag_items = self._cag_items(request, prefix_fence_rules=prefix_fence_rules)
        candidates += cag_items
        candidates.append(self._turn_fence_item(turn_fence_rules))
        candidates += self._memory_items(request)
        candidates += self._goal_items(request)
        candidates += self._caller_context_items(request)
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
            summary_text = f"Earlier in this conversation: {summary}"
            candidates.append(
                ContextItem(
                    content=summary_text,
                    section=ContextSection.HISTORY,
                    channel=TrustChannel.SYSTEM,
                    relevance=0.8,
                    priority=40,
                    tokens=self._tokenizer.count(summary_text),
                    provenance="episodic_summary",
                    item_id="history_summary",
                )
            )

        # Reserve CAG tokens before packing the variable tail so the 400-token cap still holds.
        cag_reserved = sum(i.tokens for i in cag_items)
        history_tokens = sum(cost for _, cost in history)
        user_cost = self._message_cost(self._tokenizer.count(request.user_message))
        remaining = budget.total - history_tokens - user_cost - cag_reserved
        # `pack()` drops -- never truncates -- a pinned item that does not fit ("a pinned item
        # that cannot fit at all is a configuration error", domain/context.py). The flat
        # `+ 64` floor below was sized for "some slack for the variable tail", not for what the
        # already-pinned candidates (the system prompt, CAG fence rules, and the turn-nonce
        # fence rules ADR-006's DATA-channel fencing depends on) actually cost. A long-running
        # Engineer session (real conversation history + a persona system prompt padded with
        # many tool descriptions) can drive `remaining` toward zero, collapsing the packing
        # budget to that bare floor -- far smaller than the pinned candidates -- so the model
        # silently generated with no system prompt, no persona, and no trust-channel fencing at
        # all. Confirmed live: a real Engineer turn logged "context items dropped for budget"
        # with used=0 -- every pinned candidate dropped, not just the variable tail. Reserving
        # every already-pinned candidate's own cost as an additional floor guarantees the
        # highest-priority items (processed first in `pack()`) always have room to fit.
        pinned_reserved = sum(item.tokens for item in candidates if item.is_pinned)
        packing_budget = ContextBudget(
            total=max(
                budget.reserved_for_output + 64,
                remaining + cag_reserved,
                pinned_reserved + budget.reserved_for_output,
            ),
            reserved_for_output=budget.reserved_for_output,
            section_caps=budget.section_caps,
        )

        bundle = pack(candidates, packing_budget)
        # Carry both nonces onto the packed bundle so rendered fences match the rules text.
        bundle = ContextBundle(
            items=bundle.items,
            dropped=bundle.dropped,
            truncated=bundle.truncated,
            budget_total=bundle.budget_total,
            tokens_used=bundle.tokens_used,
            fence_nonce=seed.fence_nonce,
            prefix_nonce=prefix_nonce,
        )

        shape = _HistoryShape(
            omitted=dropped_turns,
            had_summary=bool(summary),
            markers=self._marker_costs(
                sum(1 for m in request.history if m.role is not Role.SYSTEM)
            ),
        )
        bundle, fitted = self._enforce_limit(request, bundle, history, user_cost, shape)
        prompt_tokens = fitted.total
        usable, _, _ = self._window_limits(request)
        limit = self._prompt_limit(request)
        over_cap = prompt_tokens > limit
        if prompt_tokens > usable - _MIN_OUTPUT_TOKENS:
            # Nothing left to remove, and the provider would reject (or silently truncate) a
            # prompt this size: fail the turn with a reason instead of sending it.
            raise ContextOverflowError(
                "the message and required instructions do not fit the model's context window",
                details={
                    "prompt_tokens": prompt_tokens,
                    "context_window": request.context_window,
                    "max_prompt_tokens": limit,
                },
            )
        if over_cap:
            # Only the irreducible part is left (instructions, fence rules, pinned notes, the
            # user's message) and it alone exceeds the prefill cap. It fits the window, so it
            # is sent -- refusing would turn a slow turn into a failed one -- but not silently.
            logger.warning(
                "assembled prompt exceeds the prompt cap after trimming",
                extra={"prompt_tokens": prompt_tokens, "cap": limit},
            )
        # Clamp the answer to what the window leaves after this prompt (KAN-99).
        max_output_tokens = max(1, min(request.max_output_tokens, usable - prompt_tokens))

        system_text = bundle.render(include_sections=_SYSTEM_SECTIONS)
        # The history message: the summary (if it fit), then one line saying what of the
        # conversation is in neither the summary nor the verbatim turns below (KAN-100).
        history_parts = []
        if fitted.keep_block:
            history_parts.append(bundle.render(include_sections=(ContextSection.HISTORY,)))
        omitted = shape.omitted + fitted.dropped_messages
        summary_dropped = shape.had_summary and not fitted.keep_block
        if omitted or summary_dropped:
            history_parts.append(_omission_marker(omitted, summary_dropped))
        history_text = "\n".join(history_parts)
        conversation = Conversation.of(
            [
                Message.system(system_text),
                *([Message.system(history_text)] if history_text else []),
                *(message for message, _ in fitted.kept),
                Message.user(request.user_message),
            ]
        )
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
            dropped=(
                len(bundle.dropped) + dropped_turns + fitted.dropped_messages
                + int(fitted.summary_dropped) + fitted.items_removed
            ),
            truncated=len(bundle.truncated),
            max_output_tokens=max_output_tokens,
            over_cap=over_cap,
            omitted_messages=omitted,
        )

    # -- cap enforcement -----------------------------------------------------------------

    def _enforce_limit(
        self,
        request: ContextRequest,
        bundle: ContextBundle,
        history: Sequence[tuple[Message, int]],
        user_cost: int,
        shape: "_HistoryShape",
    ) -> tuple[ContextBundle, "_HistoryFit"]:
        """Bring the assembled prompt within the turn's prompt limit (KAN-99).

        Eviction order, cheapest loss first: the oldest history messages, then the history
        summary block (both in :meth:`_fit_to_cap`), then recalled memory and goals, then
        retrieved knowledge and caller evidence, then tool output -- truncated head-and-tail
        first, dropped only when even that cannot fit -- then unpinned scratchpad notes. After
        each removal history is refitted, so room an item frees goes back to the conversation.

        What cannot be removed -- pinned items, the omission marker, and the user's message --
        may still exceed the limit; :meth:`assemble` decides whether that is sendable.
        """
        count = self._tokenizer.count
        limit = self._prompt_limit(request)

        def fit(candidate: ContextBundle) -> _HistoryFit:
            return self._fit_to_cap(
                system_cost=self._message_cost(
                    candidate.rendered_tokens(count, include_sections=_SYSTEM_SECTIONS)
                ),
                block_tokens=(
                    candidate.rendered_tokens(count, include_sections=(ContextSection.HISTORY,))
                    if candidate.by_section(ContextSection.HISTORY)
                    else 0
                ),
                history=history,
                user_cost=user_cost,
                cap=limit,
                shape=shape,
            )

        fitted = fit(bundle)
        if fitted.total <= limit:
            return bundle, fitted

        removed = 0
        for section in (
            ContextSection.MEMORY,
            ContextSection.KNOWLEDGE,
            ContextSection.TOOL_OUTPUT,
            ContextSection.SCRATCHPAD,
        ):
            while fitted.total > limit:
                victims = [i for i in bundle.items if i.section is section and not i.is_pinned]
                if not victims:
                    break
                victim = min(victims, key=lambda i: (i.priority, i.value_density))
                shortened = None
                if section is ContextSection.TOOL_OUTPUT:
                    keep = victim.tokens - (fitted.total - limit)
                    if keep >= _MIN_TRUNCATED_TOKENS:
                        shortened = victim.truncated_to(keep)
                bundle = _replace_item(bundle, victim, shortened)
                if shortened is None:
                    removed += 1
                fitted = fit(bundle)
        return bundle, replace(fitted, items_removed=removed)

    # -- history ------------------------------------------------------------------------

    def _marker_costs(self, messages: int) -> tuple[int, int]:
        """Upper bounds on the omission marker's cost, without and with a dropped summary.

        Counted once per assembly at the largest count the marker could state, which has the
        most digits, so a marker with any smaller count costs no more than this.
        """
        widest = max(1, messages)
        return (
            self._tokenizer.count(_omission_marker(widest, False)),
            self._tokenizer.count(_omission_marker(widest, True)),
        )

    def _history_message_cost(
        self, block_tokens: int, keep_block: bool, omitted: int, summary_dropped: bool,
        shape: "_HistoryShape",
    ) -> int:
        """The history system message: the summary block, if kept, and the marker line, if
        anything of the conversation is in neither the summary nor the verbatim turns."""
        marker = 0
        if omitted or summary_dropped:
            marker = shape.markers[1] if summary_dropped else shape.markers[0]
        content = (block_tokens if keep_block else 0) + marker
        if not content:
            return 0
        if keep_block and block_tokens and marker:
            content += self._separator
        return self._message_cost(content)

    def _fit_to_cap(
        self,
        *,
        system_cost: int,
        block_tokens: int,
        history: Sequence[tuple[Message, int]],
        user_cost: int,
        cap: int | None,
        shape: "_HistoryShape",
    ) -> "_HistoryFit":
        """Drop oldest history until the assembled prompt fits ``cap``.

        RAG and instructions stay. History is already in the session store; trimming it here
        only changes what the model sees this turn. Works on costs alone: each part was counted
        once, and dropping one subtracts its cost rather than recounting the prompt (KAN-105).

        Every message dropped here joins those the window already left out, and the marker
        line that says so is costed in, so a dropped message is never unaccounted for (KAN-100).
        """
        history_msgs = list(history)
        keep_block = block_tokens > 0
        dropped = 0

        def header() -> int:
            return self._history_message_cost(
                block_tokens, keep_block, shape.omitted + dropped,
                shape.had_summary and not keep_block, shape,
            )

        total = (
            self._priming + system_cost + header()
            + sum(cost for _, cost in history_msgs) + user_cost
        )
        if not cap:
            return _HistoryFit(history_msgs, keep_block, total)
        while total > cap and history_msgs:
            before = header()
            _, cost = history_msgs.pop(0)
            dropped += 1
            total += header() - before - cost
        summary_dropped = False
        if total > cap and keep_block:
            before = header()
            keep_block = False
            summary_dropped = True
            total += header() - before
        return _HistoryFit(history_msgs, keep_block, total, dropped, summary_dropped)

    # -- helpers -----------------------------------------------------------------------

    def count(self, text: str) -> int:
        return self._tokenizer.count(text)

    @property
    def tokenizer_name(self) -> str:
        return self._tokenizer.name

    @property
    def counts_are_exact(self) -> bool:
        return self._tokenizer.exact


@dataclass(frozen=True, slots=True)
class _HistoryShape:
    """What history assembly knows before fitting: how many messages the window or the history
    allowance already left out, whether a summary was supplied, and the marker's costs."""

    omitted: int
    had_summary: bool
    markers: tuple[int, int]


@dataclass(frozen=True, slots=True)
class _HistoryFit:
    """One fit's outcome. ``dropped_messages``/``summary_dropped`` count what the cap fit
    removed; ``items_removed`` what cap enforcement removed from the bundle."""

    kept: list[tuple[Message, int]]
    keep_block: bool
    total: int
    dropped_messages: int = 0
    summary_dropped: bool = False
    items_removed: int = 0


def _omission_marker(omitted: int, summary_dropped: bool) -> str:
    """The one line that accounts for conversation the model cannot see (KAN-100).

    Every message the session holds is verbatim in the prompt, folded into the summary, or
    counted here -- so "what did I say earlier?" gets "I can't see that part" rather than a
    confident answer built on a silently missing turn.
    """
    if omitted and summary_dropped:
        return (
            f"[{omitted} earlier message{'s' if omitted != 1 else ''} of this conversation, "
            "and a summary of what came before them, are not shown here.]"
        )
    if omitted:
        verb = "are" if omitted != 1 else "is"
        plural = "s" if omitted != 1 else ""
        return f"[{omitted} earlier message{plural} of this conversation {verb} not shown here.]"
    return "[A summary of the earlier conversation is not shown here.]"


def _replace_item(
    bundle: ContextBundle, victim: ContextItem, shortened: ContextItem | None
) -> ContextBundle:
    """``bundle`` with ``victim`` replaced by ``shortened``, or moved to ``dropped``."""
    index = next(i for i, item in enumerate(bundle.items) if item is victim)
    if shortened is None:
        return replace(
            bundle,
            items=bundle.items[:index] + bundle.items[index + 1 :],
            dropped=bundle.dropped + (victim,),
            tokens_used=bundle.tokens_used - victim.tokens,
        )
    label = victim.item_id or victim.provenance or victim.section.value
    return replace(
        bundle,
        items=bundle.items[:index] + (shortened,) + bundle.items[index + 1 :],
        truncated=bundle.truncated if label in bundle.truncated else bundle.truncated + (label,),
        tokens_used=bundle.tokens_used - victim.tokens + shortened.tokens,
    )


def _clip_chars(text: str, max_chars: int) -> str:
    """Bound a retrieved chunk for the prompt without changing the stored document."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    budget = max(16, max_chars - len(_CLIP_MARKER))
    cut = text[:budget]
    if " " in cut[-32:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + _CLIP_MARKER
