"""``ContextGroundedProvider``: an ``EchoProvider`` variant that answers from retrieved context.

Phase A3's own follow-up names the gap this closes: ``AnswerEvaluator``'s groundedness and
citation-precision scoring needs "a real or scripted provider" because the default
``EchoProvider`` echoes the user's own message, which never shares vocabulary with retrieved
content by construction. This provider extracts the fenced ``<<<AURA-DATA:...>>>`` blocks
Context Engine wraps retrieved chunks in and answers with excerpts from them instead.

The one property that matters most here is negative: the excerpt must never reproduce eight or
more consecutive source words, because ``SystemPromptLeakGuard`` treats exactly that pattern as a
leaked system prompt (the fenced knowledge is itself part of the system prompt) and blocks the
whole answer, which silently zeroes every score downstream. A real bug of exactly this shape was
found and fixed while building this provider -- joining per-sentence excerpts with a plain space
reconstructed a long real run across the sentence boundary, since two sentences pulled from the
same block are themselves adjacent in the source.
"""

from __future__ import annotations

import re

from aura_core.domain.llm import CompletionRequest
from aura_core.domain.messages import Conversation, Message
from aura_core.llm.providers.echo import ContextGroundedProvider


def _fence(content: str, *, nonce: str = "abc123", source: str = "doc:x") -> str:
    return (
        f'<<<AURA-DATA:{nonce} source="{source}">>>\n{content}\n<<<END-AURA-DATA:{nonce}>>>'
    )


def _request(system_text: str, user_message: str = "a question") -> CompletionRequest:
    return CompletionRequest(
        conversation=Conversation.of(
            [Message.system(system_text), Message.user(user_message)]
        )
    )


def _max_consecutive_source_words(answer: str, source: str) -> int:
    """The longest run of consecutive words the answer shares with the source, in source order.

    Mirrors ``SystemPromptLeakGuard._shingles`` closely enough to catch the same class of bug:
    it is the guard's own n-gram overlap check, generalised to "what is the longest run" instead
    of "are there two 8-grams".
    """
    answer_words = answer.lower().split()
    source_words = source.lower().split()
    best = 0
    for start in range(len(answer_words)):
        for length in range(1, len(answer_words) - start + 1):
            window = " ".join(answer_words[start : start + length])
            if window in " ".join(source_words):
                best = max(best, length)
            else:
                break
    return best


class TestContextGroundedProvider:
    async def test_falls_back_to_plain_echo_without_a_data_fence(self) -> None:
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        request = _request("## Instructions\nBe helpful.", "hello there")
        response = await provider.complete(request)
        assert response.text == "hello there"

    async def test_extracts_an_answer_from_the_fenced_block(self) -> None:
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        source = (
            "Annual leave is 21 days per year for all permanent staff. Requests must be "
            "submitted at least 14 days in advance through the HR portal."
        )
        request = _request(f"## Knowledge\n{_fence(source)}", "how much leave do I get?")
        response = await provider.complete(request)

        assert response.text.strip()
        assert response.text != "how much leave do I get?"
        # The words are drawn from the source -- this is what makes the answer "grounded".
        answer_words = set(re.findall(r"[a-z0-9']+", response.text.lower()))
        source_words = set(re.findall(r"[a-z0-9']+", source.lower()))
        assert answer_words & source_words

    async def test_never_reproduces_eight_consecutive_source_words(self) -> None:
        """Regression test: an earlier version joined per-sentence excerpts with a plain space,
        silently reconstructing a long run across the sentence boundary (two sentences pulled
        from one block are themselves adjacent in the source) and tripping
        ``SystemPromptLeakGuard`` -- which discards the entire answer, not just the offending
        part, zeroing every downstream score."""
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        source = (
            "The existing system has structured logs, optional Sentry, and optional OTLP "
            "tracing, all generic web service observability. It has zero AI specific telemetry, "
            "no token counts, no cost, no per provider latency, no cache hit rate at all today."
        )
        request = _request(f"## Knowledge\n{_fence(source)}", "what telemetry exists?")
        response = await provider.complete(request)

        assert _max_consecutive_source_words(response.text, source) < 8

    async def test_handles_multiple_fenced_blocks(self) -> None:
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        first = _fence(
            "The director of Finance Operations approves expenses above the threshold.",
            nonce="aaa111", source="doc:handbook",
        )
        second = _fence(
            "Sick leave is uncapped but requires a medical certificate after three days.",
            nonce="bbb222", source="doc:handbook2",
        )
        request = _request(f"## Knowledge\n{first}\n{second}", "tell me about leave and expenses")
        response = await provider.complete(request)

        assert response.text.strip()

    async def test_a_source_heading_containing_a_literal_gt_does_not_break_the_fence_parse(
        self,
    ) -> None:
        """Regression test: the ``source="..."`` attribute can itself contain a literal ``>``
        (a section heading path like "kernel > Context" -- aura_core/domain/context.py), which
        an earlier ``[^>]*>>>`` header pattern stopped at prematurely and never matched the fence
        at all."""
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        source = "Telemetry is a first class output of the kernel for every real turn processed."
        fence = _fence(source, source="adr-009@v1 (ADR-009 > Context)")
        request = _request(f"## Knowledge\n{fence}", "what does the ADR say?")
        response = await provider.complete(request)

        assert response.text.strip()
        assert response.text != "what does the ADR say?"

    async def test_ignores_non_data_bearing_system_content(self) -> None:
        provider = ContextGroundedProvider(name="echo", model="echo-1")
        request = _request(
            "## Instructions\nYou are AURA. Be precise, concise, and honest.",
            "what should I do today?",
        )
        response = await provider.complete(request)
        assert response.text == "what should I do today?"
