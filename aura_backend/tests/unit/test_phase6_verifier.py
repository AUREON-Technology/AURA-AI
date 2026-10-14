"""The ADR-030 Phase 6 driver still runs against the code it verifies.

``deploy/scripts/verify_hosted_inference.py`` only ever runs on the VPS, by hand, with a real
key. Nothing else exercises it, so a rename in the router, the quota tracker or the kernel would
first surface as a crash in the middle of the one run meant to produce evidence. This test runs
the whole driver in-process, through the real ``Container``, against an echo provider that plays
a hosted one. It proves the driver works. It proves nothing about any real provider: that is
what the driver's report is for.

The one fake: a hosted slot is flagged third-party from its base URL's host, and a test cannot
use a hosted host, so the flag is set by hand after the container is built. The provider also
answers ``429`` once a few large prompts have arrived, the way a per-minute token limit would.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
from pathlib import Path

import pytest

import aura_core.container as container_module
from aura_core.config.settings import load_settings
from aura_core.domain.errors import ProviderError
from aura_core.domain.llm import DataClassification

DRIVER = Path(__file__).resolve().parents[3] / "deploy" / "scripts" / "verify_hosted_inference.py"

#: Large prompts the fake serves before answering 429. The driver's burst sends ~2,000 tokens each.
_LARGE_PROMPTS_BEFORE_429 = 2


def _load_driver():
    spec = importlib.util.spec_from_file_location("verify_hosted_inference", DRIVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _play_hosted(provider) -> None:
    provider._capabilities = dataclasses.replace(
        provider.capabilities,
        third_party=True,
        is_local=False,
        max_data_classification=DataClassification.INTERNAL,
    )
    served_large = 0
    complete = provider.complete

    async def rate_limited_complete(request):
        nonlocal served_large
        if request.conversation.approx_tokens() > 1000:
            served_large += 1
            if served_large > _LARGE_PROMPTS_BEFORE_429:
                raise ProviderError(
                    "429 Too Many Requests", provider=provider.name,
                    details={"status": 429, "retry_after_s": 30.0},
                )
        return await complete(request)

    provider.complete = rate_limited_complete


@pytest.fixture
def hosted_stack(monkeypatch: pytest.MonkeyPatch):
    env = {
        "AURA_LLM_PROVIDERS": "hosted1,local",
        "AURA_LLM_ROUTING_POLICY": "quality_first",
        "AURA_PROVIDER_HOSTED1_KIND": "echo",
        "AURA_PROVIDER_HOSTED1_MODEL": "phase6-fake",
        "AURA_PROVIDER_HOSTED1_QUALITY_TIER": "3",
        "AURA_PROVIDER_HOSTED1_RPD": "1000",
        "AURA_PROVIDER_LOCAL_KIND": "echo",
        "AURA_PROVIDER_LOCAL_IS_LOCAL": "true",
        "AURA_PROVIDER_LOCAL_QUALITY_TIER": "1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    base = container_module.Container

    class HostedContainer(base):
        def __init__(self, _settings):
            # The driver asks for the cached settings; this test's env needs a fresh read.
            super().__init__(load_settings(refresh=True, validate=False))
            _play_hosted(self.router.get("hosted1"))

    monkeypatch.setattr(container_module, "Container", HostedContainer)
    monkeypatch.delenv("AURA_VERIFY_SKIP_429", raising=False)
    yield
    load_settings(refresh=True, validate=False)


def test_every_scripted_check_passes_against_a_well_behaved_provider(hosted_stack, capsys) -> None:
    driver = _load_driver()

    failures = asyncio.run(driver.main())

    output = capsys.readouterr().out
    statuses = {number: status for number, _name, status in driver._results}
    assert failures == 0, output
    assert statuses == {**{str(n): "PASS" for n in range(1, 11)}, "11": "MANUAL"}, output
    assert any("429 after" in line for line in driver._observations), output


def test_skipping_the_burst_is_reported_not_passed(hosted_stack, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AURA_VERIFY_SKIP_429", "1")
    driver = _load_driver()

    failures = asyncio.run(driver.main())

    statuses = {number: status for number, _name, status in driver._results}
    assert failures == 0
    assert statuses["10"] == "SKIP"
    assert "some were skipped" in capsys.readouterr().out
