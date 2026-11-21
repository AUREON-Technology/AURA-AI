"""KAN-27 "Context Window - Improve": budgeting, history coverage, and tool output that never
disappears silently. One class per subtask (KAN-98 ... KAN-102, KAN-105)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from aura_core.context import ContextEngine, ContextRequest, HeuristicTokenizer
from aura_core.domain import Conversation, Message
from aura_core.domain.context import ContextSection


class CountingTokenizer:
    """The heuristic tokenizer, recording every string it is asked to count."""

    def __init__(self) -> None:
        self._inner = HeuristicTokenizer()
        self.seen: Counter[str] = Counter()

    @property
    def name(self) -> str:
        return "counting"

    @property
    def exact(self) -> bool:
        return False

    def count(self, text: str) -> int:
        self.seen[text] += 1
        return self._inner.count(text)

    def count_messages(self, messages: Sequence[Any]) -> int:
        for message in messages:
            self.seen[str(getattr(message, "content", ""))] += 1
        return self._inner.count_messages(messages)


def _history(exchanges: int) -> Conversation:
    messages: list[Message] = []
    for i in range(exchanges):
        messages.append(Message.user(f"question number {i} about the quarterly report"))
        messages.append(Message.assistant(f"answer number {i}: the figures are in section {i}"))
    return Conversation.of(messages)


class TestCountEachItemOnce:
    """KAN-105: history was counted three times per assembly, the system prompt twice, and the
    cap fit recounted the whole prompt after every dropped message."""

    def _assemble(self, tokenizer: CountingTokenizer, **overrides: Any):
        request = dict(
            system_prompt="You are AURA. Answer briefly.",
            user_message="What did we decide?",
            history=_history(6),
            history_summary="The user asked about Q1 and Q2 figures.",
            tool_results=(("calculator", "2 + 2 = 4"),),
        )
        request.update(overrides)
        return ContextEngine(tokenizer=tokenizer, history_turns=4).assemble(
            ContextRequest(**request)
        )

    def test_each_history_message_is_counted_at_most_once(self) -> None:
        tokenizer = CountingTokenizer()
        self._assemble(tokenizer)
        for message in _history(6):
            assert tokenizer.seen[message.content] <= 1, message.content

    def test_history_is_counted_once_even_when_the_cap_drops_messages(self) -> None:
        tokenizer = CountingTokenizer()
        long_history = Conversation.of(
            [Message.user("tell me more " * 30), Message.assistant("here is more " * 30)] * 3
        )
        assembled = self._assemble(tokenizer, history=long_history, max_prompt_tokens=200)
        assert assembled.prompt_tokens <= 200
        for message in long_history:
            assert tokenizer.seen[message.content] <= 1

    def test_the_system_prompt_and_user_message_are_counted_once(self) -> None:
        tokenizer = CountingTokenizer()
        self._assemble(tokenizer)
        assert tokenizer.seen["You are AURA. Answer briefly."] == 1
        assert tokenizer.seen["What did we decide?"] == 1

    def test_the_stored_count_matches_counting_the_rendered_prompt(self) -> None:
        """Summing stored parts must stay an honest estimate of the prompt actually sent."""
        assembled = self._assemble(CountingTokenizer(), max_prompt_tokens=0)
        actual = HeuristicTokenizer().count_messages(list(assembled.conversation))
        assert abs(assembled.prompt_tokens - actual) <= max(4, actual // 50)

    def test_rendered_tokens_matches_a_count_of_render(self) -> None:
        assembled = self._assemble(CountingTokenizer(), max_prompt_tokens=0)
        counter = HeuristicTokenizer()
        sections = (ContextSection.INSTRUCTIONS, ContextSection.TOOL_OUTPUT)
        estimate = assembled.bundle.rendered_tokens(counter.count, include_sections=sections)
        actual = counter.count(assembled.bundle.render(include_sections=sections))
        assert abs(estimate - actual) <= 4
