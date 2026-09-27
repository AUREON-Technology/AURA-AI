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

from ..domain.cag import CagPrefix
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

    # -- budgeting ---------------------------------------------------------------------

    def budget_for(self, request: ContextRequest) -> ContextBudget:
        """Derive the budget from the provider's window and the prompt cap.

        Reserves output space plus a 5 % margin against the provider window. A separate
        ``max_prompt_tokens`` cap bounds *prefill* independently of the window -- otherwise a
        8k-context local model would still ingest 1k+ token RAG prompts that CPU Ollama cannot
        evaluate before the stream timeout.
        """
        usable = int(request.context_window * 0.95)
        reserved = min(request.max_output_tokens, max(256, usable // 4))
        cap = self._effective_prompt_cap(request)
        if cap is None:
            return ContextBudget(total=usable, reserved_for_output=reserved)
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
        fields -- no title or description, which are zero-knowledge ciphertext this process
        cannot read (see ``infrastructure/db/models.py``'s ``Goal``). A goal a model cannot name
        is still worth knowing the shape of: how urgent, how important, how far along.
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

        dropped_by_window = 0
        if self._history_turns > 0 and len(messages) > self._history_turns:
            dropped_by_window = len(messages) - self._history_turns
            messages = messages[-self._history_turns :]

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

        dropped = dropped_by_window + (len(messages) - len(kept))
        summary = request.history_summary
        # Do not invent an omission notice: it costs a system message and tokens that the
        # CPU prefill budget needs for RAG. An operator-supplied episodic summary still lands.
        return Conversation.of(kept), summary, dropped

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

        # Reserve CAG tokens before packing the variable tail so the 400-token cap still holds.
        cag_reserved = sum(i.tokens for i in cag_items)
        history_tokens = sum(self._tokenizer.count(m.content) + 4 for m in history)
        user_tokens = self._tokenizer.count(request.user_message) + 4
        remaining = budget.total - history_tokens - user_tokens - cag_reserved
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

        system_text = bundle.render(
            include_sections=(
                ContextSection.INSTRUCTIONS,
                ContextSection.CAG,
                ContextSection.MEMORY,
                ContextSection.KNOWLEDGE,
                ContextSection.TOOL_OUTPUT,
                ContextSection.SCRATCHPAD,
            )
        )
        history_text = bundle.render(include_sections=(ContextSection.HISTORY,))

        cap = self._effective_prompt_cap(request)
        conversation, prompt_tokens, trimmed = self._fit_to_cap(
            system_text=system_text,
            history_text=history_text,
            history=history,
            user_message=request.user_message,
            cap=cap,
        )
        if cap and prompt_tokens > cap and bundle.by_section(ContextSection.MEMORY):
            # Memory lost the budget fight: keep instructions + CAG + citations.
            system_text = bundle.render(
                include_sections=(
                    ContextSection.INSTRUCTIONS,
                    ContextSection.CAG,
                    ContextSection.KNOWLEDGE,
                    ContextSection.TOOL_OUTPUT,
                    ContextSection.SCRATCHPAD,
                )
            )
            conversation, prompt_tokens, trimmed_more = self._fit_to_cap(
                system_text=system_text,
                history_text=history_text,
                history=history,
                user_message=request.user_message,
                cap=cap,
            )
            trimmed += trimmed_more
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
            dropped=len(bundle.dropped) + dropped_turns + trimmed,
            truncated=len(bundle.truncated),
        )

    # -- helpers -----------------------------------------------------------------------

    def _fit_to_cap(
        self,
        *,
        system_text: str,
        history_text: str,
        history: Conversation,
        user_message: str,
        cap: int | None,
    ) -> tuple[Conversation, int, int]:
        """Drop oldest history until the assembled prompt fits ``cap``.

        RAG and instructions stay. History is already in the session store; trimming it here
        only changes what the model sees this turn.
        """
        extra_system = [Message.system(history_text)] if history_text else []
        history_msgs = list(history)
        dropped = 0

        def build() -> list[Message]:
            return [
                Message.system(system_text),
                *extra_system,
                *history_msgs,
                Message.user(user_message),
            ]

        messages = build()
        prompt_tokens = self._tokenizer.count_messages(messages)
        if not cap:
            return Conversation.of(messages), prompt_tokens, 0
        while prompt_tokens > cap and history_msgs:
            history_msgs.pop(0)
            dropped += 1
            messages = build()
            prompt_tokens = self._tokenizer.count_messages(messages)
        if prompt_tokens > cap and extra_system:
            extra_system.clear()
            dropped += 1
            messages = build()
            prompt_tokens = self._tokenizer.count_messages(messages)
        return Conversation.of(messages), prompt_tokens, dropped

    def count(self, text: str) -> int:
        return self._tokenizer.count(text)

    @property
    def tokenizer_name(self) -> str:
        return self._tokenizer.name

    @property
    def counts_are_exact(self) -> bool:
        return self._tokenizer.exact


def _clip_chars(text: str, max_chars: int) -> str:
    """Bound a retrieved chunk for the prompt without changing the stored document."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    budget = max(16, max_chars - len(_CLIP_MARKER))
    cut = text[:budget]
    if " " in cut[-32:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip() + _CLIP_MARKER
