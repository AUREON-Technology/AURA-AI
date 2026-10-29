"""A completion request that names no deadline gets the router's configured one (F-22).

``CompletionRequest.timeout_s`` used to default to a hard-coded 60 s. Every caller that did not
set it -- the reasoner (which serves every non-streaming /chat), the session summariser, entity
extraction, query expansion -- was cancelled at 60 s, while a 1024-token answer needs 100-200 s at
the CPU stack's 5-10 tok/s.
"""

from __future__ import annotations

from aura_core.domain import CompletionRequest, Conversation, Message
from aura_core.llm import ProviderRouter
from aura_core.llm.providers.echo import EchoProvider


class _Recording(EchoProvider):
    def __init__(self) -> None:
        super().__init__(name="local")
        self.seen: list[CompletionRequest] = []

    async def complete(self, request):
        self.seen.append(request)
        return await super().complete(request)


def _request(**kwargs) -> CompletionRequest:
    return CompletionRequest(conversation=Conversation.of([Message.user("hi")]), **kwargs)


async def test_an_unset_deadline_is_the_routers_configured_one() -> None:
    provider = _Recording()
    router = ProviderRouter([provider], default_timeout_s=180.0)

    await router.complete(_request())

    assert provider.seen[0].timeout_s == 180.0


async def test_an_explicit_deadline_is_kept() -> None:
    provider = _Recording()
    router = ProviderRouter([provider], default_timeout_s=180.0)

    await router.complete(_request(timeout_s=12.0))

    assert provider.seen[0].timeout_s == 12.0


def test_the_container_router_uses_the_configured_llm_timeout(container) -> None:
    assert container.router._default_timeout_s == container.settings.llm.request_timeout_s
