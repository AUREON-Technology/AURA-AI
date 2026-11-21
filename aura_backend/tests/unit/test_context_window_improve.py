"""KAN-27 "Context Window - Improve": budgeting, history coverage, and tool output that never
disappears silently. One class per subtask (KAN-98 ... KAN-102, KAN-105)."""

from __future__ import annotations

from collections import Counter
from typing import Any, Sequence

from aura_core.config.settings import AuraSettings, LLMSettings, ProviderConfig
from aura_core.context import ContextEngine, ContextRequest, HeuristicTokenizer
from aura_core.domain import (
    CompletionRequest,
    Conversation,
    Message,
    ProviderCapabilities,
    SamplingParams,
)
from aura_core.domain.context import ContextSection
from aura_core.domain.errors import ProviderError
from aura_core.domain.identifiers import UserId
from aura_core.domain.llm import RoutingPolicy, WorkloadClass
from aura_core.kernel.kernel import AIKernel, KernelDependencies, TurnRequest
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider, ScriptedTurn
from aura_core.llm.workload import workload_scope


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


# -- KAN-98 ------------------------------------------------------------------------------


def _provider(name: str, *, window: int, workloads=(), max_output: int = 1024, **caps: Any):
    return EchoProvider(
        name=name, model=name,
        capabilities=ProviderCapabilities(
            model_id=name, context_window=window, max_output_tokens=max_output,
            supports_tools=True, workloads=frozenset(workloads), **caps,
        ),
    )


def _kernel(providers, *, global_cap: int, provider_caps=None, **router_kw: Any):
    configs = tuple(
        ProviderConfig(
            name=p.name, kind="echo", context_window=p.capabilities.context_window,
            max_output_tokens=p.capabilities.max_output_tokens,
            max_prompt_tokens=(provider_caps or {}).get(p.name, 0),
        )
        for p in providers
    )
    settings = AuraSettings(llm=LLMSettings(providers=configs, max_prompt_tokens=global_cap))
    router = ProviderRouter(list(providers), **router_kw)
    kernel = AIKernel(
        KernelDependencies(router=router, prompts=None, context=None, settings=settings)
    )
    return kernel, router


def _sizing(kernel: AIKernel, request: TurnRequest) -> tuple[int, int]:
    """The window and cap the kernel hands the context engine, under the turn's scopes."""
    with workload_scope(kernel._workload_hint(request)):
        return kernel._context_window(request), kernel._max_prompt_tokens(request)


_CHAT = (WorkloadClass.INTERACTIVE, WorkloadClass.VOICE)


def _fleet() -> list[EchoProvider]:
    """Railway's shape: an Engineer-only long-context slot and two chat-sized ones."""
    return [
        _provider("claude1", window=200_000, max_output=8192, workloads=(WorkloadClass.ENGINEER,)),
        _provider("hosted1", window=8192, workloads=_CHAT),
        _provider("hfspace", window=16_384, workloads=_CHAT),
    ]


class TestWorkloadAwareWindowAndPerProviderCap:
    def test_an_engineer_turn_gets_the_long_context_providers_cap(self) -> None:
        kernel, _ = _kernel(
            _fleet(), global_cap=200_000,
            provider_caps={"claude1": 150_000, "hosted1": 4096, "hfspace": 4096},
        )
        window, cap = _sizing(
            kernel,
            TurnRequest(message="refactor", user_id=UserId("u"), workload=WorkloadClass.ENGINEER),
        )
        assert window == 200_000
        assert cap >= 100_000

    def test_a_chat_turn_uses_the_healthy_minimum_of_its_own_providers(self) -> None:
        kernel, _ = _kernel(
            _fleet(), global_cap=200_000,
            provider_caps={"claude1": 150_000, "hosted1": 4096, "hfspace": 6000},
        )
        window, cap = _sizing(kernel, TurnRequest(message="hi", user_id=UserId("u")))
        assert window == 8192  # hosted1 -- not the Engineer-only 200K slot
        assert cap == 4096

    def test_the_global_cap_bounds_every_per_provider_cap(self) -> None:
        kernel, _ = _kernel(_fleet(), global_cap=400, provider_caps={"hosted1": 4096})
        assert _sizing(kernel, TurnRequest(message="hi", user_id=UserId("u")))[1] == 400

    def test_an_undeclared_cap_means_the_global(self) -> None:
        kernel, _ = _kernel(_fleet(), global_cap=400)
        assert _sizing(kernel, TurnRequest(message="hi", user_id=UserId("u")))[1] == 400

    async def test_an_unhealthy_provider_does_not_shrink_the_window(self) -> None:
        tiny = EchoProvider(
            name="tiny", model="tiny",
            script=[ScriptedTurn(raise_error=ProviderError("down", provider="tiny", retryable=True))],
            capabilities=ProviderCapabilities(
                model_id="tiny", context_window=2048, max_output_tokens=512, quality_tier=3,
                workloads=frozenset(_CHAT),
            ),
        )
        kernel, router = _kernel(
            [*_fleet(), tiny], global_cap=4096, circuit_failure_threshold=1,
            default_policy=RoutingPolicy.QUALITY_FIRST,
        )
        request = TurnRequest(message="hi", user_id=UserId("u"))
        assert _sizing(kernel, request)[0] == 2048  # healthy, it is the smallest

        await router.complete(CompletionRequest(
            conversation=Conversation.of([Message.user("hi")]),
            sampling=SamplingParams(temperature=0.0),
        ))
        assert router.plan(CompletionRequest(
            conversation=Conversation.of([Message.user("hi")])
        )).excluded.get("tiny") == "circuit_open"
        assert _sizing(kernel, request)[0] == 8192


class TestPerProviderPromptCapSetting:
    def test_parsed_from_the_provider_env(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_KIND", "echo")
        monkeypatch.setenv("AURA_PROVIDER_CLAUDE1_MAX_PROMPT_TOKENS", "150000")
        assert ProviderConfig.from_env("claude1").max_prompt_tokens == 150_000

    def test_undeclared_is_zero(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_PROVIDER_HOSTED9_KIND", "echo")
        monkeypatch.delenv("AURA_PROVIDER_HOSTED9_MAX_PROMPT_TOKENS", raising=False)
        assert ProviderConfig.from_env("hosted9").max_prompt_tokens == 0

    def test_a_long_context_global_is_accepted(self, monkeypatch) -> None:
        monkeypatch.setenv("AURA_LLM_MAX_PROMPT_TOKENS", "150000")
        assert LLMSettings.from_env().max_prompt_tokens == 150_000
