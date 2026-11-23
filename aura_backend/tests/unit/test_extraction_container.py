"""``extract_structured`` wired into the container and the real tool registry (ADR-038).

Off is the default and means no adapter and no tool. On, the real Cloudflare adapter is built (and
makes no call until used). The end-to-end cases swap in the in-memory extractor by replacing the
container's one builder method, so the registry, policy, data scope and audit sink are all real.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from aura_core.adapters.cloudflare_clairic import ClairicExtractor
from aura_core.adapters.memory_extractor import InMemoryStructuredExtractor
from aura_core.config.settings import AuraSettings, ExtractionSettings, load_settings
from aura_core.container import Container
from aura_core.domain.errors import ConfigurationError
from aura_core.domain.identifiers import ToolCallId, UserId
from aura_core.domain.llm import DataClassification
from aura_core.domain.messages import ToolCall
from aura_core.llm.sensitivity import current_data_scope, data_scope
from aura_core.ports.runtime import ToolContext
from aura_core.tools.extract import EXTRACT_AUDIT_ACTION, EXTRACT_TOOL_NAME

ACCOUNT = "0123456789abcdef0123456789abcdef"
TOKEN = "cfat_not_a_real_token_value"
ENV = "AURA_TEST_EXTRACT_TOKEN"
TEXT = "Invoice number: INV-2291\nTotal: $1,080.00\n"
ARGS = {"text": TEXT, "fields": {"invoice_number": {"type": "text"}, "total": {"type": "number"}}}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ALLOW_EXTRACTION_INFERENCE", "EXTRACT_CLOUDFLARE_ACCOUNT_ID", "EXTRACT_API_KEY_ENV"):
        monkeypatch.delenv(f"AURA_{name}", raising=False)
    monkeypatch.delenv(ENV, raising=False)


def _settings(**extraction) -> AuraSettings:
    base = load_settings(refresh=True, validate=False)
    block = dict(enabled=True, account_id=ACCOUNT, api_key_env=ENV)
    block.update(extraction)
    return dataclasses.replace(base, extraction=ExtractionSettings(**block))


def _ctx(**kw) -> ToolContext:
    return ToolContext(user_id=UserId("user-1"), correlation_id="corr-1", deadline_s=30.0, **kw)


def _call(call_id: str = "c1") -> ToolCall:
    return ToolCall(id=ToolCallId(call_id), name=EXTRACT_TOOL_NAME, arguments=ARGS)


class TestOffByDefault:
    async def test_there_is_no_extractor_no_tool_and_health_says_so(self, container: Container) -> None:
        assert container.structured_extractor is None
        assert EXTRACT_TOOL_NAME not in container.tools
        assert (await container.health())["extraction"] == {"enabled": False}


class TestEnabled:
    def test_the_real_adapter_is_built_and_the_tool_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, TOKEN)
        container = Container(_settings())
        assert isinstance(container.structured_extractor, ClairicExtractor)
        assert EXTRACT_TOOL_NAME in container.tools
        spec = container.tools.get(EXTRACT_TOOL_NAME).spec
        assert "untrusted_content" in spec.tags and not spec.side_effecting
        assert container.structured_extractor._client is None, "construction must not touch the network"

    def test_the_configured_limits_reach_the_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, TOKEN)
        container = Container(_settings(max_chars=777, timeout_s=12.0))
        spec = container.tools.get(EXTRACT_TOOL_NAME).spec
        assert next(p for p in spec.parameters if p.name == "text").max_length == 777
        assert spec.timeout_s == 17.0

    async def test_health_reports_it_without_secrets_and_without_gating_readiness(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(ENV, TOKEN)
        payload = await Container(_settings()).health()
        assert payload["extraction"] == {
            "enabled": True, "model": "@cf/cloudflare/clairic", "cooling_down": False}
        text = json.dumps(payload)
        assert TOKEN not in text and ACCOUNT not in text
        assert payload["healthy"] is True

    def test_a_missing_credential_is_a_boot_failure_not_a_silent_tool(self) -> None:
        with pytest.raises(ConfigurationError):
            Container(_settings())

    def test_a_bad_account_id_is_a_boot_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ENV, TOKEN)
        with pytest.raises(ConfigurationError):
            Container(_settings(account_id="not-an-account-id"))


class _Spy(InMemoryStructuredExtractor):
    closed = 0

    async def close(self) -> None:
        type(self).closed += 1


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch):
    """A container whose extractor is the in-memory one; everything else is real."""
    extractor = _Spy()
    _Spy.closed = 0
    monkeypatch.setattr(Container, "_build_structured_extractor", lambda self: extractor)
    return Container(load_settings(refresh=True, validate=False)), extractor


class TestThroughTheRealRegistry:
    async def test_a_turn_holding_only_the_persons_words_is_served_and_audited(self, wired) -> None:
        container, extractor = wired
        with data_scope(DataClassification.INTERNAL):
            result, _ = await container.tools.execute(_call(), _ctx())
        assert result.ok, result.content
        answers = json.loads(result.content)["answers"]
        assert answers["invoice_number"]["value"] == "INV-2291" and answers["total"]["value"] == 1080
        assert extractor.calls == [len(TEXT)]
        (event,) = await container.audit.query(action=EXTRACT_AUDIT_ACTION)
        assert (event.actor, event.outcome) == ("user-1", "success")

    async def test_the_registry_raises_the_scope_and_taints_so_a_second_call_is_refused(self, wired) -> None:
        container, extractor = wired
        ctx = _ctx()
        with data_scope(DataClassification.INTERNAL):
            first, _ = await container.tools.execute(_call("c1"), ctx)
            assert first.ok and ctx.tainted, "its output is untrusted content"
            assert current_data_scope().level is DataClassification.SENSITIVE
            second, _ = await container.tools.execute(_call("c2"), ctx)
        assert not second.ok and "tool_output" in second.content
        assert extractor.calls == [len(TEXT)], "the second call must send nothing"

    async def test_with_no_data_scope_it_is_refused_through_the_registry_too(self, wired) -> None:
        container, extractor = wired
        result, _ = await container.tools.execute(_call(), _ctx())
        assert not result.ok and extractor.calls == []

    async def test_an_agent_without_the_tool_in_its_grant_cannot_call_it(self, wired) -> None:
        container, extractor = wired
        with data_scope(DataClassification.INTERNAL):
            result, _ = await container.tools.execute(
                _call(), _ctx(allowed_tools=frozenset({"calculator"}))
            )
        assert not result.ok and extractor.calls == []

    async def test_shutdown_closes_the_extractor(self, wired) -> None:
        container, _ = wired
        await container.shutdown()
        assert _Spy.closed == 1
