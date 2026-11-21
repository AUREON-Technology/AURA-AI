"""One token estimator for the router, the quota and the context engine (KAN-104).

The router's window check and the quota reservation counted ``len/4``; the context engine
counted with the heuristic tokenizer; a provider with no reported usage fell back to ``len/4``
again. Three numbers for one prompt, and the low one decided whether a person's daily allowance
let an Arabic request through. These pin that the three now agree, using the routed provider's
own tokenizer, and that the allowance sees the real cost.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from aura_core.adapters.runtime import FrozenClock
from aura_core.context.engine import ContextEngine, ContextRequest
from aura_core.context.tokenizer import tokenizer_for
from aura_core.domain import CompletionRequest, Conversation, Message, ProviderCapabilities
from aura_core.domain.errors import UserQuotaExhaustedError
from aura_core.domain.llm import CompletionResponse, SamplingParams, WorkloadClass
from aura_core.llm import ProviderRouter, QuotaLimits, QuotaTracker
from aura_core.llm.providers.echo import EchoProvider
from aura_core.llm.workload import hint_for, workload_scope

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)

ARABIC = (
    "تهدف هذه الوثيقة إلى شرح طريقة عمل النظام بشكل واضح ومختصر، بحيث يستطيع أي موظف جديد أن "
    "يفهم الخطوات الأساسية دون الحاجة إلى تدريب طويل. يبدأ المستخدم بتسجيل الدخول إلى حسابه، ثم "
    "يختار المشروع الذي يريد العمل عليه من القائمة الرئيسية. بعد ذلك تظهر لوحة التحكم التي تعرض "
    "المهام المفتوحة والتقارير الأخيرة والتنبيهات المهمة. "
) * 5


def _wall(clock: FrozenClock):
    return lambda: clock.now().timestamp()


class _Observing(EchoProvider):
    """Reports no usage of its own, so the response carries the provider's usage fallback, and
    records what the quota had reserved at the moment the request was sent."""

    def __init__(self, router_box: dict[str, Any], **kw: Any) -> None:
        super().__init__(**kw)
        self._box = router_box

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        router: ProviderRouter = self._box["router"]
        self._box["reserved"] = router.quota.snapshot(self.name)["day_tokens"]
        usage = self._estimate_usage(request, "ok")
        return CompletionResponse(text="ok", provider=self.name, model="qwen2.5", usage=usage)


def _caps(**kw: Any) -> ProviderCapabilities:
    base = dict(model_id="qwen2.5:7b-instruct", context_window=32768, max_output_tokens=256,
                tokenizer="qwen2")
    base.update(kw)
    return ProviderCapabilities(**base)


class TestOneFigure:
    async def test_router_quota_context_and_usage_fallback_agree(self) -> None:
        clock = FrozenClock(_START)
        box: dict[str, Any] = {}
        provider = _Observing(box, name="space", capabilities=_caps())
        quota = QuotaTracker({"space": QuotaLimits(tpd=1_000_000)}, wall=_wall(clock))
        router = ProviderRouter([provider], clock=clock.monotonic, quota=quota)
        box["router"] = router

        context = ContextEngine(tokenizer=tokenizer_for("qwen2"), max_prompt_tokens=0)
        assembled = context.assemble(ContextRequest(
            system_prompt="You are a careful assistant. Answer in the user's language.",
            user_message=ARABIC,
            history=Conversation.of([Message.user("مرحبا"), Message.assistant("أهلا بك")]),
            context_window=32768,
            max_output_tokens=64,
        ))
        request = CompletionRequest(
            conversation=assembled.conversation,
            sampling=SamplingParams(temperature=0.0, max_output_tokens=64),
        )

        # One estimator: the router counts with the routed provider's tokenizer, which is the
        # one the context engine was built with, and its figure is that tokenizer's count of
        # the prompt actually sent.
        assert router.tokenizer("space").name == context.tokenizer_name == "heuristic:qwen2"
        figure = router.prompt_tokens(request, "space")
        assert figure == tokenizer_for("qwen2").count_messages(list(assembled.conversation))
        # The context engine reaches the same figure without recounting the prompt: each part
        # is counted once and the rendered system block is costed from its items' counts plus
        # framing (KAN-105), so it can differ by a rounding step per part -- the bound
        # test_context_window_improve pins for a rendered block -- but never by the 2x a
        # len/4 count was off on Arabic.
        assert abs(assembled.prompt_tokens - figure) <= 4, (assembled.prompt_tokens, figure)
        assert router._estimated_tokens(request, provider) == figure + 64

        response = await router.complete(request)
        # The quota reserved exactly the router's figure plus the output cap...
        assert box["reserved"] == figure + 64
        # ...and the provider's no-usage fallback reports the same prompt figure, marked.
        assert response.usage.prompt_tokens == figure
        assert response.usage.estimated

    def test_each_provider_counts_with_its_own_tokenizer(self) -> None:
        qwen = EchoProvider(name="q", capabilities=_caps(tokenizer="qwen2"))
        claude = EchoProvider(name="c", capabilities=_caps(model_id="claude-x", tokenizer=""))
        router = ProviderRouter([qwen, claude])
        request = CompletionRequest(conversation=Conversation.of([Message.user(ARABIC)]))
        assert router.tokenizer("q").name == "heuristic:qwen2"
        assert router.tokenizer("c").name == "heuristic:claude"
        assert router.prompt_tokens(request, "c") > router.prompt_tokens(request, "q")

    def test_the_figure_is_far_above_len_over_four(self) -> None:
        router = ProviderRouter([EchoProvider(name="q", capabilities=_caps())])
        request = CompletionRequest(conversation=Conversation.of([Message.user(ARABIC)]))
        assert router.prompt_tokens(request, "q") > 1.5 * (len(ARABIC) // 4)


class TestUserAllowanceSeesTheRealCost:
    async def test_an_arabic_request_over_the_remaining_allowance_is_refused(self) -> None:
        clock = FrozenClock(_START)
        provider = EchoProvider(name="hosted", capabilities=_caps(max_output_tokens=32))
        request = CompletionRequest(
            conversation=Conversation.of([Message.user(ARABIC)]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=32),
        )
        len4_cost = sum((len(m.content) + 3) // 4 + 4 for m in request.conversation) + 32
        router_probe = ProviderRouter([provider])
        real_cost = router_probe._estimated_tokens(request, provider)
        allowance = (len4_cost + real_cost) // 2
        # The old len/4 figure fits the allowance; the real cost does not.
        assert len4_cost < allowance < real_cost

        quota = QuotaTracker({"hosted": QuotaLimits(user_tpd=allowance)}, wall=_wall(clock))
        router = ProviderRouter([provider], clock=clock.monotonic, quota=quota)
        with workload_scope(hint_for(WorkloadClass.ENGINEER, user_key="alice")):
            with pytest.raises(UserQuotaExhaustedError):
                await router.complete(request)
        assert provider.calls == []

    async def test_the_same_allowance_admits_an_english_request_of_that_size(self) -> None:
        clock = FrozenClock(_START)
        provider = EchoProvider(name="hosted", capabilities=_caps(max_output_tokens=32))
        quota = QuotaTracker({"hosted": QuotaLimits(user_tpd=2_000)}, wall=_wall(clock))
        router = ProviderRouter([provider], clock=clock.monotonic, quota=quota)
        request = CompletionRequest(
            conversation=Conversation.of([Message.user("Please summarise the attached notes.")]),
            sampling=SamplingParams(temperature=0.0, max_output_tokens=32),
        )
        with workload_scope(hint_for(WorkloadClass.ENGINEER, user_key="alice")):
            response = await router.complete(request)
        assert response.text is not None and len(provider.calls) == 1
